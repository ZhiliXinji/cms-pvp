#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2017 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
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

"""A service that assigns a score to submission results.

"""

import logging
import datetime
import random

from cms import ServiceCoord, config
from cms.db import SessionGen, Submission, Dataset, get_submission_results
from cms.io import Executor, TriggeredService, rpc_method
from cmscommon.datetime import make_datetime
from .pvpoperations import PvPOperation, get_operations


logger = logging.getLogger(__name__)


from cms import utf8_decoder, ServiceCoord
from cms.db import (
    SessionGen,
    Task,
    Submission,
    SubmissionResult,
    MatchResult,
    Participation,
    File,
    Match,
    Batch
)
from cmscommon.datetime import make_datetime
from cms.io import RemoteServiceClient
from sqlalchemy.orm import aliased
from pvpoperations import get_last_match, get_last_submission, \
    get_operations, get_match_submission

logger = logging.getLogger(__name__)

SYS_ELO = "elo"

class Elo:
    players = {}

    def __init__(self, participation_ids):
        self.players = {i: 1200.0 for i in participation_ids}

    @staticmethod
    def expected_score(rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    @staticmethod
    def update_elo(rating, expected, actual, k=32):
        return rating + k * (actual - expected)

    def update_scores(self, player_a, player_b, result):
        expected_a = Elo.expected_score(self.players[player_a], self.players[player_b])
        expected_b = Elo.expected_score(self.players[player_b], self.players[player_a])

        self.players[player_a] = self.update_elo(
            self.players[player_a], expected_a, result
        )
        self.players[player_b] = self.update_elo(
            self.players[player_b], expected_b, 1.0 - result
        )

match_mode = SYS_ELO

class PvPExecutor(Executor):
    def __init__(self, scoring_service, evaluation_service):
        super().__init__()
        self.scoring_service = scoring_service
        self.evaluation_service = evaluation_service

    def next_batch(self, task):
        with SessionGen() as session:
            task.pvp_batch += 1
            session.commit()

    def create_match(self, timestamp, s1, s2, batch):
        """TODO: docstring."""
        with SessionGen() as session:
            if not s1 or not s2 or s1.task_id != s2.task_id:
                return False

            task = Task.get_from_id(s1.task_id)
            if not task or task.active_dataset.task_type != "pvp":
                return False

        return Match(
                    submission1=s1,
                    submission2=s2,
                    task=task,
                    timestamp=make_datetime(timestamp),
                    batch=task.pvp_batch,
                    batch_eval_id=batch.id
                )

    def mark_match_submissions(self, task):
        """Mark the submissions that will be used in the match."""
        with SessionGen() as session:
            for p in task.contest.participations:
                submission = get_last_submission(session, p, task)
                if submission:
                    submission.pvp_batch = task.pvp_batch
                    result = submission.get_result_or_create()
                    result.invalidate_score()
            session.commit()
            return True

    def start_batch(self, batch_id):
        with SessionGen() as session:
            batch = Batch.get_from_id(batch_id, session)
            if not batch:
                logger.error("No batch %d found." % batch_id)
                return False

            task = batch.task
            if task.active_dataset.task_type != "PvP":
                logger.error("Not a PvP task. Exiting.")
                return False

            if not self.next_batch(task):
                return False
            if not self.mark_match_submissions(task):
                return False

            # round-robin
            # if match_mode == "round-robin":
            #     # total_matches = {p.id: 0.0 for p in task.contest.participations}
            #     # win_matches = {p.id: 0.0 for p in task.contest.participations}
            #     # for match in matches:
            #     #     if match.result.get_status() != MatchResult.SCORED:
            #     #         print("Match %d is not scored." % match.id)
            #     #         return False
            #     #     total_matches[match.submission1.participation.id] += 1.0
            #     #     total_matches[match.submission2.participation.id] += 1.0
            #     #     win_matches[match.submission1.participation.id] += match.result.score
            #     #     win_matches[match.submission2.participation.id] += (
            #     #         1.0 - match.result.score
            #     #     )

            #     # for p in task.contest.participations:
            #     #     if total_matches[p.id] != 0.0:
            #     #         final_scores[p.id] = win_matches[p.id] / total_matches[p.id]
            #     for p1 in task.contest.participations:
            #         for p2 in task.contest.participations:
            #             if p1.id != p2.id:
            #                 self.add_match(task, time.time(), p1, p2)
            # elif match_mode == SYS_ELO:

            participation = []
            rounds = batch.rounds
            batch.start_evaluate()

            for p in task.contest.participations:
                submission = get_match_submission(session, p, task)
                if submission is not None:
                    participation.append((p, submission))
            num = len(participation)
            batch.total_match = 0

            # XXX: Random pairing?
            matches = []
            for _ in range(rounds):
                random.shuffle(participation)
                for i in range(0, num - 1, 2):
                    s1, s2 = participation[i][1], participation[i + 1][1]
                    matches.append(self.create_match(make_datetime(), s1, s2, batch))
            batch.total_match = len(matches)

            for match in matches:
                session.add(match)
                session.add(match.get_result_or_create())

            session.commit()

            for match in matches:
                self.evaluation_service.new_match(match.id)

            if batch.total_match == 0:
                # TODO: process with this situation
                batch.end_evaluate()

        return True

    def execute(self, entry):
        """Execute a batch evaluation, or update all scores.

        This is the core of PvPService: here we retrieve the batch
        from the database, check if it is in the correct status,
        instantiate its ScoreType, compute its score, store it back in
        the database and tell ProxyService to update RWS if needed.

        entry (QueueEntry): entry containing the operation to perform.

        """
        operation = entry.item
        with SessionGen() as session:
            batch = Batch.get_from_id(operation.batch_id,
                                                session)

            task = Task.get_from_id(batch.task_id, session)
            if task is None:
                raise ValueError("Task %d not found in the database." %
                                 operation.task_id)

            dataset = task.active_dataset
            if dataset.task_type != "PvP":
                raise ValueError("Task %d is not a PvP task." % task.id)

            self.start_batch(batch.id)


class PvPService(TriggeredService):
    """A service that processes all operations related to PvP task.

    """

    def __init__(self, shard):
        """Initialize the PvPService.

        """
        super().__init__(shard)

        # Set up communication with SS and ES.
        self.scoring_service = self.connect_to(
            ServiceCoord("ScoringService", 0))
        self.evaluation_service = self.connect_to(
            ServiceCoord("EvaluationService", 0))

        self.add_executor(PvPExecutor(self.scoring_service, self.evaluation_service))
        self.start_sweeper(29.0)

    def _missing_operations(self):
        """Return a generator of unprocessed batch evaluation request.

        Obtain a list of all the ranking requests in the database,
        check each of them to see if it's still unprocessed and if so
        enqueue them.

        """
        counter = 0
        with SessionGen() as session:
            for operation, timestamp in get_operations(session, make_datetime()):
                self.enqueue(operation, timestamp=timestamp)
                counter += 1
        return counter

    def update_single_score(self, task, participation, testcase, score, text):
        """TODO: docstring."""
        with SessionGen() as session:
            logger.info(
                "participation %d get %f on testcase %s",
                participation.id,
                score,
                testcase.codename,
            )
            submission = get_match_submission(session, participation, task)

            if not submission:
                return False

            submission_result = submission.get_result()
            if not submission_result:
                return False

            evaluation = submission_result.get_evaluation(testcase)
            evaluation.outcome = score
            evaluation.text = text

            submission_result.set_evaluation_outcome()
            session.commit()

            self.scoring_service.new_evaluation(
                submission_id=submission_result.submission_id,
                dataset_id=submission_result.dataset_id,
            )

    def update_score(self, task, competition_sys):
        with SessionGen() as session:
            for tc in task.active_dataset.testcases.values():
                sorted_players = sorted(
                    competition_sys[tc.id].players.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
                all_num = len(sorted_players)
                for rank, (participation_id, score) in enumerate(sorted_players, start=1):
                    new_score = 1.0 / rank
                    self.update_single_score(
                        task,
                        session.query(Participation).get(participation_id),
                        tc,
                        new_score,
                        [
                            "Your rating: %0.2f. Rank among all participations: %d/%d."
                            % (
                                competition_sys[tc.id].players[participation_id],
                                rank,
                                all_num,
                            )
                        ],
                    )

            session.commit()
            return True

    def batch_ended(self, batch_id):
        with SessionGen() as session:
            batch = Batch.get_from_id(batch_id, session)
            task = batch.task
            competition_sys = {
                tc.id: Elo(
                    participation_ids=[p.id for p in task.contest.participations]
                )
                for tc in task.active_dataset.testcases.values()
            }

            for p in task.contest.participations:
                if get_match_submission(session, p, task) is None:
                    for tc in task.active_dataset.testcases.values():
                        competition_sys[tc.id].players[p.id] = 0.0

            for match in sorted(batch.matches, key=lambda m: m.timestamp):
                for matching in match.result.matchings:
                    competition_sys[matching.testcase_id].update_scores(
                        match.submission1.participation_id,
                        match.submission2.participation_id,
                        float(matching.outcome.split()[0].strip()),
                    )
            self.update_score(task, competition_sys)
            batch.end_evaluate()

    @rpc_method
    def match_ended(self, match_id):
        with SessionGen() as session:
            match = Match.get_from_id(match_id, session)
            if match is None:
                return False
            batch = match.batch
            if batch is None:
                return False
            batch.total_matches -= 1
            if batch.total_matches == 0:
                self.batch_ended(batch.id)
                session.commit()
                return True
            else:
                session.commit()
                return False

    @rpc_method
    def new_batch(self, batch_id):
        self.enqueue(PvPOperation(batch_id))
