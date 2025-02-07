#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2015-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
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

"""Utility to submit a solution for a user."""

import argparse
import logging
import sys

from cms import utf8_decoder, ServiceCoord
from cms.db import (
    File,
    Participation,
    SessionGen,
    Submission,
    Task,
    User,
    ask_for_contest,
)
from cms.db.filecacher import FileCacher
from cms.grading.languagemanager import filename_to_language
from cms.io import RemoteServiceClient
from cmscommon.datetime import make_datetime

from .StartMatch import add_match


logger = logging.getLogger(__name__)


def maybe_send_notification(submission_id):  # TODO : modify it
    """Non-blocking attempt to notify a running ES of the submission"""
    rs = RemoteServiceClient(ServiceCoord("EvaluationService", 0))
    rs.connect()
    rs.new_submission(submission_id=submission_id)
    rs.disconnect()


def language_from_submitted_files(files):
    """Return the language inferred from the submitted files.

    files ({str: str}): dictionary mapping the expected filename to a path in
        the file system.

    return (Language|None): the language inferred from the files.

    raise (ValueError): if different files point to different languages, or if
        it is impossible to extract the language from a file when it should be.

    """
    # TODO: deduplicate with the code in SubmitHandler.
    language = None
    for filename in files.keys():
        this_language = filename_to_language(files[filename])
        if this_language is None and ".%l" in filename:
            raise ValueError("Cannot recognize language for file `%s'." % filename)

        if language is None:
            language = this_language
        elif this_language is not None and language != this_language:
            raise ValueError("Mixed-language submission detected.")
    return language


def add_single_match(contest_id, username, task_name, timestamp, opponentname):
    with SessionGen() as session:
        participation = (
            session.query(Participation)
            .join(Participation.user)
            .filter(Participation.contest_id == contest_id)
            .filter(User.username == username)
            .first()
        )
        if participation is None:
            logging.critical(
                "User `%s' does not exists or does not participate in the contest.",
                username,
            )
            return False
        o_participation = (
            session.query(Participation)
            .join(Participation.user)
            .filter(Participation.contest_id == contest_id)
            .filter(User.username == opponentname)
            .first()
        )
        if o_participation is None:
            logging.critical(
                "User `%s' does not exists or does not participate in the contest.",
                username,
            )
            return False
        task = (
            session.query(Task)
            .filter(Task.contest_id == contest_id)
            .filter(Task.name == task_name)
            .first()
        )
        if task is None:
            logging.critical("Unable to find task `%s'.", task_name)
            return False


        # Create objects in the DB.
        add_match(session, task, timestamp, participation, o_participation)

    return True


def main():
    """Parse arguments and launch process.

    return (int): exit code of the program.

    """
    parser = argparse.ArgumentParser(description="Adds a match to a contest in CMS.")
    parser.add_argument(
        "-c",
        "--contest-id",
        action="store",
        type=int,
        help="id of contest where to add the match",
    )
    parser.add_argument(
        "username", action="store", type=utf8_decoder, help="user doing the submission"
    )
    parser.add_argument(
        "opponentname",
        action="store",
        type=utf8_decoder,
        help="user fight against user",
    )
    parser.add_argument(
        "task_name",
        action="store",
        type=utf8_decoder,
        help="name of task the submission is for",
    )
    parser.add_argument(
        "-t",
        "--timestamp",
        action="store",
        type=int,
        help="timestamp of the submission in seconds from "
        "epoch, e.g. `date +%%s` (now if not set)",
    )

    args = parser.parse_args()

    if args.contest_id is None:
        args.contest_id = ask_for_contest()

    if args.timestamp is None:
        import time

        args.timestamp = time.time()

    success = add_single_match(
        contest_id=args.contest_id,
        username=args.username,
        task_name=args.task_name,
        timestamp=args.timestamp,
        opponentname=args.opponentname,
    )
    return 0 if success is True else 1


if __name__ == "__main__":
    sys.exit(main())
