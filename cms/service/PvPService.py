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
from cms.db import SessionGen, Submission, Dataset, get_submission_results, Testcase
from cms.io import Executor, TriggeredService, rpc_method
from cmscommon.datetime import make_datetime
from .pvpoperations import PvPOperation
from functools import wraps
import math

import gevent.lock
from cms import utf8_decoder
from cms.db import (
    Task,
    Submission,
    SubmissionResult,
    MatchResult,
    Participation,
    File,
    Match,
    Batch,
)
from cms.io import RemoteServiceClient
from sqlalchemy.orm import aliased
from .pvpoperations import (
    get_last_match,
    get_last_submission,
    get_operations,
    get_match_submission,
)
from math import ceil

logger = logging.getLogger(__name__)

SYS_ELO = "elo"

class Elo:
    S_RATING = 1500.0

    players = {}

    def __init__(self, participation_ids):
        self.players = {i: Elo.S_RATING for i in participation_ids}

    @staticmethod
    def expected_score(rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    @staticmethod
    def update_elo(rating, expected, actual, k=32):
        return rating + k * (actual - expected)

    def update_scores(self, player_a, player_b, result1, result2, k):
        expected_a = Elo.expected_score(self.players[player_a], self.players[player_b])
        expected_b = Elo.expected_score(self.players[player_b], self.players[player_a])

        sum = result1 + result2
        result1 /= sum
        result2 /= sum

        self.players[player_a] = self.update_elo(
            self.players[player_a], expected_a, result1, k
        )
        self.players[player_b] = self.update_elo(
            self.players[player_b], expected_b, result2, k
        )

    def to_scores(self):
        div = [
            (10, 0.85, 1.00),
            (30, 0.60, 0.85),
            (30, 0.35, 0.60),
            (20, 0.20, 0.35),
            (10, 0.05, 0.20),
        ]

        def calc_score(rating, min_rating, max_rating, min_score, max_score):
            assert min_rating <= rating <= max_rating
            if min_rating == max_rating:
                return max_score
            return (max_score - min_score) * (rating - min_rating) / (
                max_rating - min_rating
            ) + min_score

        ratings = sorted(list(self.players.items()), key=lambda x: x[1], reverse=True)
        scores = []
        total_number = len(list(filter(lambda x: x[1] > 0, ratings)))

        last = 0
        sum_percent = 0
        for percent, min_score, max_score in div:
            sum_percent += percent
            pos = int(ceil(total_number * sum_percent / 100))
            if last == pos:
                continue
            max_rating = ratings[last][1]
            min_rating = ratings[pos - 1][1]
            for p_id, rating in ratings[last:pos]:
                scores.append(
                    (
                        p_id,
                        calc_score(
                            rating,
                            min_rating,
                            max_rating,
                            min_score,
                            max_score,
                        ),
                    )
                )
            last = pos

        for p in scores[last:]:
            scores.append((p[0], 0.0))

        return scores


match_mode = SYS_ELO

class PvPExecutor(Executor):
    def __init__(
        self, scoring_service, evaluation_service, participations, competition_sys
    ):
        super().__init__()
        self.scoring_service = scoring_service
        self.evaluation_service = evaluation_service
        self.participations = participations
        self.competition_sys = competition_sys

    def next_batch(self, session, task_id):
        task = Task.get_from_id(task_id, session)
        if not task:
            print("No task called `%s' found." % task.name)
            return False
        task.pvp_batch += 1
        session.commit()
        return True

    def create_match(
        self, session, timestamp, s1_id, s2_id, batch_id, testcase_id=None
    ):
        """TODO: docstring."""
        s1 = Submission.get_from_id(s1_id, session)
        s2 = Submission.get_from_id(s2_id, session)
        if not s1 or not s2 or s1.task_id != s2.task_id:
            logger.error("No match between two submissions.")
            return

        task = Task.get_from_id(s1.task_id, session)
        if not task or task.active_dataset.task_type != "PvP":
            logger.error("Not a PvP task. Exiting.")
            return

        batch = Batch.get_from_id(batch_id, session)
        testcase = Testcase.get_from_id(testcase_id, session)
        # print("***" + repr(task.id))
        match = Match(
            submission1=s1,
            submission2=s2,
            task=task,
            timestamp=timestamp,
            batch=task.pvp_batch,
            batch_eval=batch,
            testcase=testcase,
        )
        return match

    def mark_match_submissions(self, session, task_id):
        """Mark the submissions that will be used in the match, and then discard old submissions."""

        def invalidate_old_submission(submission: Submission):
            if submission.task.active_dataset.task_type != "PvP":
                return
            session = submission.sa_session()


        task = Task.get_from_id(task_id, session)
        for p in task.contest.participations:
            submission = get_last_submission(session, p, task)
            # print(submission)
            if submission:
                submission.pvp_batch = task.pvp_batch
                result = submission.get_result_or_create()
                result.invalidate_evaluation()

        invalidate_old_submission(submission)

        session.commit()
        return True

    def start_round(self, session, batch_id):
        batch = Batch.get_from_id(batch_id, session)
        if not batch:
            logger.error("No batch %d found." % batch_id)
            return False

        task = batch.task
        if task.active_dataset.task_type != "PvP":
            logger.error("Not a PvP task. Exiting.")
            return False

        logger.info("Starting batch %d." % batch_id)
        rounds = batch.rounds

        if batch.rounds_id == 0:
            # Initialization.
            if not self.next_batch(session, task.id):
                logger.error("Mark next batch failed.")
                return False
            if not self.mark_match_submissions(session, task.id):
                logger.error("Could not mark submissions for task %s." % task.name)
                return False
            if rounds == 0:
                logger.error("Zero rounds are not allowed.")
                return False

            self.participations[batch_id] = {}
            self.competition_sys[batch_id] = {
                tc.id: Elo(
                    participation_ids=[p.id for p in task.contest.participations]
                )
                for tc in task.active_dataset.testcases.values()
            }
            participations = self.participations[batch_id]
            competition_sys = self.competition_sys[batch_id]
            for p in task.contest.participations:
                submission = get_match_submission(session, p, task)
                if submission is not None:
                    participations[p.id] = submission.id
                else:
                    for tc in task.active_dataset.testcases.values():
                        competition_sys[tc.id].players[p.id] = 0.0

            if len(participations) < 2:
                logger.warning("Batch %d has less than 2 players. Exiting." % batch_id)
                return False

            batch.start_evaluate()

        elif batch.rounds_id >= batch.rounds:
            logger.info("Batch %d already evaluated. Exiting." % batch_id)
            return False

        participations = self.participations[batch_id]
        competition_sys = self.competition_sys[batch_id]
        batch.rounds_id += 1
        logger.info(
            "Starting evaluating batch %d for round %d." % (batch_id, batch.rounds_id)
        )

        assert batch.rest_matches == 0

        matches = []
        for tc in task.active_dataset.testcases.values():
            players = [
                (p_id, competition_sys[tc.id].players[p_id])
                for p_id in participations.keys()
            ]
            random.shuffle(players)

            bye_p = []
            matched = set()
            for p_id, rating in players:
                if p_id in matched:
                    continue

                initial_window = 100
                max_window = 600

                window = initial_window
                candidates = []
                # Avoid to fight against self.
                matched.add(p_id)
                while window <= max_window and not candidates:
                    candidates = list(
                        filter(
                            lambda p: abs(p[1] - rating) <= window
                            and p[0] not in matched,
                            players,
                        )
                    )
                    # Increase window if no candidates are found.
                    window += 50

                if not candidates:
                    bye_p.append(p_id)
                    continue

                opponent_id = random.choice(candidates)[0]
                s1_id, s2_id = (
                    participations[p_id],
                    participations[opponent_id],
                )
                matched.add(p_id)
                matched.add(opponent_id)
                assert s1_id is not None and s2_id is not None
                match = self.create_match(
                    session,
                    make_datetime(),
                    s1_id,
                    s2_id,
                    batch.id,
                    tc.id,
                )
                if match is not None:
                    matches.append(match)
        session.commit()

        batch.rest_matches = len(matches)
        if batch.rest_matches == 0:
            return

        for match in matches:
            session.add(match)
        session.commit()

        for match in matches:
            session.add(match.get_result_or_create())
        session.commit()

        for match in matches:
            self.evaluation_service.new_match(match_id=match.id)

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
        logger.info("Executing PvP operation %s." % operation)
        with SessionGen() as session:
            batch = Batch.get_from_id(operation.batch_id,
                                                session)

            task = Task.get_from_id(batch.task_id, session)
            if task is None:
                logger.critical(
                    "Task %d not found in the database." % operation.task_id
                )
                return

            dataset = task.active_dataset
            if dataset.task_type != "PvP":
                logger.critical("Task %d is not a PvP task." % task.id)
                return

            self.start_round(session, batch.id)


def with_post_finish_lock(func):
    """Decorator for locking on self.post_finish_lock.

    Ensures that no more than one decorated function is executing at
    the same time.

    """

    @wraps(func)
    def wrapped(self, *args, **kwargs):
        with self.post_finish_lock:
            return func(self, *args, **kwargs)

    return wrapped

# TODO: multi-contest support.
class PvPService(TriggeredService):
    """A service that processes all operations related to PvP task.

    """

    participations = {}
    competition_sys = {}

    def __init__(self, shard):
        """Initialize the PvPService.

        """
        super().__init__(shard)

        # Set up communication with SS and ES.
        self.scoring_service = self.connect_to(
            ServiceCoord("ScoringService", 0))
        self.evaluation_service = self.connect_to(
            ServiceCoord("EvaluationService", 0))

        self.add_executor(
            PvPExecutor(
                self.scoring_service,
                self.evaluation_service,
                self.participations,
                self.competition_sys,
            )
        )
        self.start_sweeper(29.0)

        self.post_finish_lock = gevent.lock.RLock()

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

    def update_single_score(self, session, submission_id, testcase, score, text):
        """
        Update the score of a submission in a certain testcase.
        """
        submission = Submission.get_from_id(submission_id, session)
        if not submission:
            return False
        logger.info(
            "submission %d get %f on testcase %s",
            submission.id,
            score,
            testcase.codename,
        )

        submission_result = submission.get_result()
        if not submission_result:
            return False

        evaluation = submission_result.get_evaluation(testcase)
        evaluation.outcome = score
        evaluation.text = text
    def update_score(self, session, task_id, participations, competition_sys):
        task = Task.get_from_id(task_id, session)
        for tc in task.active_dataset.testcases.values():
            logger.info(
                "updating score for testcase %s in task %d", tc.codename, task_id
            )
            total_num = len(participations)
            for rank, (p_id, score) in enumerate(
                competition_sys[tc.id].to_scores(), start=1
            ):
                self.update_single_score(
                    session,
                    participations[p_id],
                    tc,
                    score,
                    [
                        "Your rating: %0.2f. Rank among all participations: %d/%d."
                        % (
                            competition_sys[tc.id].players[p_id],
                            rank,
                            total_num,
                        )
                    ],
                )
            session.commit()

        submission_ids = participations.values()
        for submission_id in submission_ids:
            submission = Submission.get_from_id(submission_id, session)
            if submission is not None:
                result = submission.get_result()
                if result:
                    result.invalidate_score()
                    result.set_evaluation_outcome()
        session.commit()
        for submission_id in submission_ids:
            self.scoring_service.new_evaluation(
                submission_id=submission_id,
                dataset_id=task.active_dataset.id,
            )
        return True

    def batch_ended(self, batch_id):
        with SessionGen() as session:
            logger.info("Batch %d ended." % batch_id)

            batch = Batch.get_from_id(batch_id, session)
            task = batch.task

            self.update_score(
                session,
                task.id,
                self.participations[batch_id],
                self.competition_sys[batch_id],
            )
            batch.end_evaluate()
            session.commit()

    @rpc_method
    @with_post_finish_lock
    def match_ended(self, match_id):
        with SessionGen() as session:
            match = Match.get_from_id(match_id, session)
            if match is None:
                return False
            batch = match.batch_eval
            if batch is None:
                return False

            batch.rest_matches -= 1
            session.commit()
            assert match.testcase_id is not None
            assert len(match.result.matchings) == 1
            outcomes = match.result.matchings[0].outcome.split()
            self.competition_sys[batch.id][match.testcase_id].update_scores(
                match.submission1.participation_id,
                match.submission2.participation_id,
                float(outcomes[0].strip()),
                float(outcomes[1].strip()),
                40 * 4 ** -(batch.rounds_id / batch.rounds),
            )
            if batch.rest_matches == 0:
                if batch.rounds_id == batch.rounds:
                    self.batch_ended(batch.id)
                    return True
                else:
                    self.new_batch(batch.id)
            return False

    @rpc_method
    def new_batch(self, batch_id):
        # NOTE: this can also called to process next round in the same batch.
        self.enqueue(PvPOperation(batch_id))

    @rpc_method
    def manually_start_match(self, task_id, rounds):
        with SessionGen() as session:
            task = Task.get_from_id(task_id, session)
            if task is None:
                return False
            if task.active_dataset.task_type != "PvP":
                return False
            task_type_object = task.active_dataset.task_type_object
            if rounds == "":
                rounds = task_type_object.rounds
            new_batch = Batch(
                task=task,
                timestamp=make_datetime(),
                rounds=int(rounds),
                matches=[],
                task_pvp_batch=task.pvp_batch + 1,
            )
            session.add(new_batch)
            session.commit()
            self.new_batch(new_batch.id)
            return True
