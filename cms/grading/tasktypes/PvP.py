#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2016 Masaki Hara <ackie.h.gmai@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import os
import tempfile
from functools import reduce

from cms import config, rmtree
from cms.db import Executable
from cms.grading.ParameterTypes import ParameterTypeChoice, ParameterTypeInt
from cms.grading.Sandbox import wait_without_std, Sandbox
from cms.grading.languagemanager import LANGUAGES, get_language
from cms.grading.steps import (
    compilation_step,
    evaluation_step_before_run,
    evaluation_step_after_run,
    extract_outcome_and_text,
    human_evaluation_message,
    merge_execution_stats,
    trusted_step,
)
from cms.grading.tasktypes import check_files_number
from . import (
    TaskType,
    set_configuration_error,
    check_manager_present,
    create_sandbox,
    delete_sandbox,
    is_manager_for_compilation,
)


logger = logging.getLogger(__name__)


# Dummy function to mark translatable string.
def N_(message):
    return message


class PvP(TaskType):
    # Filename of the manager (the stand-alone, admin-provided program).
    MANAGER_FILENAME = "manager"
    # Basename of the stub, used in the stub filename and as the main class in
    # languages that require us to specify it.
    STUB_BASENAME = "stub"
    # Filename of the input in the manager sandbox. The content will be
    # redirected to stdin, and managers should read from there.
    INPUT_FILENAME = "input.txt"
    # Filename where the manager can write additional output to show to users
    # in case of a user test.
    OUTPUT_FILENAME = "output.txt"

    # Constants used in the parameter definition.
    COMPILATION_ALONE = "alone"
    COMPILATION_STUB = "stub"
    USER_IO_STD = "std_io"
    USER_IO_FIFOS = "fifo_io"

    ALLOW_PARTIAL_SUBMISSION = False

    # TODO: check if COMPILATION_STUB could work properly.
    _COMPILATION = ParameterTypeChoice(
        "Compilation",
        "compilation",
        "",
        {
            COMPILATION_ALONE: "Submissions are self-sufficient",
            COMPILATION_STUB: "Submissions are compiled with a stub",
        },
    )

    _USER_IO = ParameterTypeChoice(
        "User I/O",
        "user_io",
        "",
        {
            USER_IO_STD: "User processes read from stdin and write to stdout",
            USER_IO_FIFOS: "User processes read from and write to fifos, "
            "whose paths are given as arguments",
        },
    )

    ACCEPTED_PARAMETERS = [_COMPILATION, _USER_IO]

    @property
    def name(self):
        """See TaskType.name."""
        return "PvP"

    def __init__(self, parameters):
        super().__init__(parameters)

        self.compilation = self.parameters[0]
        self.io = self.parameters[1]

    def get_compilation_commands(self, submission_format):
        """See TaskType.get_compilation_commands."""
        codenames_to_compile = []
        if self._uses_stub():
            codenames_to_compile.append(self.STUB_BASENAME + ".%l")
        codenames_to_compile.extend(submission_format)
        res = dict()
        for language in LANGUAGES:
            source_ext = language.source_extension
            executable_filename = self._executable_filename(submission_format, language)
            res[language.name] = language.get_compilation_commands(
                [
                    codename.replace(".%l", source_ext)
                    for codename in codenames_to_compile
                ],
                executable_filename,
            )
        return res

    def get_user_managers(self):
        """See TaskType.get_user_managers."""
        if self._uses_stub():
            return [self.STUB_BASENAME + ".%l"]
        else:
            return []

    def get_auto_managers(self):
        """See TaskType.get_auto_managers."""
        return [self.MANAGER_FILENAME]

    def _uses_stub(self):
        return self.compilation == self.COMPILATION_STUB

    def _uses_fifos(self):
        return self.io == self.USER_IO_FIFOS

    @staticmethod
    def _executable_filename(codenames, language):
        """Return the chosen executable name computed from the codenames.

        codenames ([str]): submission format or codename of submitted files,
            may contain %l.
        language (Language): the programming language of the submission.

        return (str): a deterministic executable name.

        """
        name = "_".join(sorted(codename.replace(".%l", "") for codename in codenames))
        return name + language.executable_extension

    # NOTE: In PvP, you have two users, so you may have to call this twice.
    # You should guarantee that two executables have different filenames.
    def compile(self, job, file_cacher):
        """See TaskType.compile."""
        language = get_language(job.language)
        source_ext = language.source_extension

        if not check_files_number(job, 1, or_more=True):
            return

        # Prepare the files to copy in the sandbox and to add to the
        # compilation command.
        filenames_to_compile = []
        filenames_and_digests_to_get = {}
        # The stub, that must have been provided (copy and add to compilation).
        if self._uses_stub():
            stub_filename = self.STUB_BASENAME + source_ext
            if not check_manager_present(job, stub_filename):
                return
            filenames_to_compile.append(stub_filename)
            filenames_and_digests_to_get[stub_filename] = job.managers[
                stub_filename
            ].digest
        # User's submitted file(s) (copy and add to compilation).
        for codename, file_ in job.files.items():
            filename = codename.replace(".%l", source_ext)
            filenames_to_compile.append(filename)
            filenames_and_digests_to_get[filename] = file_.digest
        # Any other useful manager (just copy).
        for filename, manager in job.managers.items():
            if is_manager_for_compilation(filename, language):
                filenames_and_digests_to_get[filename] = manager.digest

        # Prepare the compilation command
        executable_filename = self._executable_filename(job.files.keys(), language)
        commands = language.get_compilation_commands(
            filenames_to_compile, executable_filename
        )

        # Create the sandbox.
        sandbox = create_sandbox(file_cacher, name="compile")
        job.sandboxes.append(sandbox.get_root_path())

        # Copy all required files in the sandbox.
        for filename, digest in filenames_and_digests_to_get.items():
            sandbox.create_file_from_storage(filename, digest)

        # Run the compilation.
        box_success, compilation_success, text, stats = compilation_step(
            sandbox, commands
        )

        # Retrieve the compiled executables.
        job.success = box_success
        job.compilation_success = compilation_success
        job.text = text
        job.plus = stats
        if box_success and compilation_success:
            digest = sandbox.get_file_to_storage(
                executable_filename,
                "Executable %s for %s" % (executable_filename, job.info),
            )
            job.executables[executable_filename] = Executable(
                executable_filename, digest
            )

        # Cleanup.
        delete_sandbox(sandbox, job.success, job.keep_sandbox)

    def match(self, job, file_cacher):
        """See TaskType.match."""

        # PvP means 2 users.
        players = 2

        def check_executables_number(job, n):
            def fail():
                msg = (
                    "submission contains %d executables, exactly %d are expected; "
                    "consider invalidating compilations."
                )
                set_configuration_error(job, msg, len(job.executables_list), n)

            if len(job.executables_list) != n:
                fail()
                return False
            for executables in job.executables_list:
                if len(executables) != 1:
                    fail()
                    return False
            return True

        if not check_executables_number(job, players):
            return

        indices = range(players)

        executable_filenames = [
            next(iter(job.executables_list[i].keys())) for i in indices
        ]
        executable_digests = [
            job.executables_list[i][executable_filenames[i]].digest for i in indices
        ]

        # Make sure the required manager is among the job managers.
        if not check_manager_present(job, self.MANAGER_FILENAME):
            return
        manager_digest = job.managers[self.MANAGER_FILENAME].digest

        # Create FIFOs.
        fifo_dir = [tempfile.mkdtemp(dir=config.temp_dir) for i in indices]
        fifo_user_to_manager = [
            os.path.join(fifo_dir[i], "u%d_to_m" % i) for i in indices
        ]
        fifo_manager_to_user = [
            os.path.join(fifo_dir[i], "m_to_u%d" % i) for i in indices
        ]
        for i in indices:
            os.mkfifo(fifo_user_to_manager[i])
            os.mkfifo(fifo_manager_to_user[i])
            os.chmod(fifo_dir[i], 0o755)
            os.chmod(fifo_user_to_manager[i], 0o666)
            os.chmod(fifo_manager_to_user[i], 0o666)
        # Names of the fifos after being mapped inside the sandboxes.
        sandbox_fifo_dir = ["/fifo%d" % i for i in indices]
        sandbox_fifo_user_to_manager = [
            os.path.join(sandbox_fifo_dir[i], "u%d_to_m" % i) for i in indices
        ]
        sandbox_fifo_manager_to_user = [
            os.path.join(sandbox_fifo_dir[i], "m_to_u%d" % i) for i in indices
        ]

        # Create the manager sandbox and copy manager and input.
        sandbox_mgr = create_sandbox(file_cacher, name="manager_evaluate")
        job.sandboxes.append(sandbox_mgr.get_root_path())
        sandbox_mgr.create_file_from_storage(
            self.MANAGER_FILENAME, manager_digest, executable=True
        )
        sandbox_mgr.create_file_from_storage(self.INPUT_FILENAME, job.input)

        # Create the user sandbox(es) and copy the executable.
        sandbox_user = [
            create_sandbox(file_cacher, name="user_evaluate") for i in indices
        ]
        job.sandboxes.extend(s.get_root_path() for s in sandbox_user)
        for i in indices:
            sandbox_user[i].create_file_from_storage(
                executable_filenames[i], executable_digests[i], executable=True
            )

        # Start the manager. Redirecting to stdin is unnecessary, but for
        # historical reasons the manager can choose to read from there
        # instead than from INPUT_FILENAME.
        manager_command = ["./%s" % self.MANAGER_FILENAME]
        for i in indices:
            manager_command += [
                sandbox_fifo_user_to_manager[i],
                sandbox_fifo_manager_to_user[i],
            ]
        # We could use trusted_step for the manager, since it's fully
        # admin-controlled. But trusted_step is only synchronous at the moment.
        # Thus we use evaluation_step, and we set a time limit generous enough
        # to prevent user programs from sending the manager in timeout.
        # This means that:
        # - the manager wall clock timeout must be greater than the sum of all
        #     wall clock timeouts of the user programs;
        # - with the assumption that the work the manager performs is not
        #     greater than the work performed by the user programs, the manager
        #     user timeout must be greater than the maximum allowed total time
        #     of the user programs; in theory, this is the task's time limit,
        #     but in practice is num_processes times that because the
        #     constraint on the total time can only be enforced after all user
        #     programs terminated.
        manager_time_limit = max(
            players * (job.time_limit + 1.0),
            config.trusted_sandbox_max_time_s,
        )
        manager = evaluation_step_before_run(
            sandbox_mgr,
            manager_command,
            manager_time_limit,
            config.trusted_sandbox_max_memory_kib * 1024,
            dirs_map=dict((fifo_dir[i], (sandbox_fifo_dir[i], "rw")) for i in indices),
            writable_files=[self.OUTPUT_FILENAME],
            stdin_redirect=self.INPUT_FILENAME,
            multiprocess=job.multithreaded_sandbox,
        )

        # Start the user submissions compiled with the stub.
        languages = [get_language(job.language_list[i]) for i in indices]
        main = [
            (
                self.STUB_BASENAME
                if self._uses_stub()
                else os.path.splitext(executable_filenames[i])[0]
            )
            for i in indices
        ]
        processes = [None for i in indices]
        for i in indices:
            args = []
            stdin_redirect = None
            stdout_redirect = None
            if self._uses_fifos():
                args.extend(
                    [sandbox_fifo_manager_to_user[i], sandbox_fifo_user_to_manager[i]]
                )
            else:
                stdin_redirect = sandbox_fifo_manager_to_user[i]
                stdout_redirect = sandbox_fifo_user_to_manager[i]
            commands = languages[i].get_evaluation_commands(
                executable_filenames[i], main=main[i], args=args
            )
            # Assumes that the actual execution of the user solution is the
            # last command in commands, and that the previous are "setup"
            # that don't need tight control.
            if len(commands) > 1:
                trusted_step(sandbox_user[i], commands[:-1])
            # XXX: What if different languages have different time limits?
            processes[i] = evaluation_step_before_run(
                sandbox_user[i],
                commands[-1],
                job.time_limit,
                job.memory_limit,
                dirs_map={fifo_dir[i]: (sandbox_fifo_dir[i], "rw")},
                stdin_redirect=stdin_redirect,
                stdout_redirect=stdout_redirect,
                multiprocess=job.multithreaded_sandbox,
            )

        # Wait for the processes to conclude, without blocking them on I/O.
        wait_without_std(processes + [manager])

        # Get the results of the manager sandbox.
        box_success_mgr, evaluation_success_mgr, unused_stats_mgr = (
            evaluation_step_after_run(sandbox_mgr)
        )

        # Coalesce the results of the user sandboxes.
        user_results = [evaluation_step_after_run(s) for s in sandbox_user]
        box_success_user = [r[0] for r in user_results]
        evaluation_success_user = [r[1] for r in user_results]
        stats_user = [r[2] for r in user_results]

        success = all(box_success_user) and box_success_mgr and evaluation_success_mgr
        outcome = None
        text = None

        # If at least one sandbox had problems, or the manager did not
        # terminate correctly, we report an error (and no need for user stats).
        if not success:
            stats_user = None

        # If just asked to execute, fill text and set dummy outcome.
        elif job.only_execution:
            outcome = "0.0 0.0"
            text = [N_("Execution completed successfully")]

        # If any user sandbox detected some problem (timeout, ...),
        # the outcome is 0.0 and the text describes that problem.
        elif not all(evaluation_success_user):
            if evaluation_success_user[0]:
                outcome = "1.0 0.0"
                text = [N_("You win because the opponent's program failed to execute")]
            elif evaluation_success_user[1]:
                outcome = "0.0 1.0"
                text = [
                    N_(
                        "You lose because your program failed to execute. The info of your program is:\n"
                    )
                    + human_evaluation_message(stats_user[0])[0]
                ]
            else:
                outcome = "0.0 0.0"
                text = [
                    N_(
                        "All user programs failed to execute. The info of your program is:\n"
                    )
                    + human_evaluation_message(stats_user[0])[0]
                ]

        # Otherwise, we use the manager to obtain the outcome.
        else:
            outcome, text = extract_outcome_and_text(sandbox_mgr, PvP=True)

        # If asked so, save the output file with additional information,
        # provided that it exists.
        if job.get_output:
            if sandbox_mgr.file_exists(self.OUTPUT_FILENAME):
                job.user_output = sandbox_mgr.get_file_to_storage(
                    self.OUTPUT_FILENAME,
                    "Output file in job %s" % job.info,
                    trunc_len=100 * 1024,
                )
            else:
                job.user_output = None

        # Fill in the job with the results.
        job.success = success
        job.outcome = "%s" % outcome if outcome is not None else None
        job.text = text
        job.plus = None if stats_user is None else stats_user[0]

        delete_sandbox(sandbox_mgr, job.success, job.keep_sandbox)
        for s in sandbox_user:
            delete_sandbox(s, job.success, job.keep_sandbox)
        if job.success and not config.keep_sandbox and not job.keep_sandbox:
            for d in fifo_dir:
                rmtree(d)

