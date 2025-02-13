#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2013-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
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

"""Utility to update the final score of a PvP task."""

import argparse
import sys
import time
import logging

from cms import utf8_decoder, ServiceCoord
from cms.db import (
    SessionGen,
    Task,
    Submission,
    MatchResult,
    Participation,
    File,
    Match,
)
from cmscommon.datetime import make_datetime
from cms.io import RemoteServiceClient
from sqlalchemy.orm import aliased
from .StartMatch import get_match_submission

logger = logging.getLogger(__name__)
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

        self.players[player_a] = Elo.update_elo(
            self.players[player_a], expected_a, result
        )
        self.players[player_b] = Elo.update_elo(
            self.players[player_b], expected_b, 1.0 - result
        )

def get_last_match(session, participation1, participation2, task):
    sub1 = aliased(Submission)
    sub2 = aliased(Submission)

    last_match = (
        session.query(Match)
        .join(sub1, Match.submission1)
        .join(sub2, Match.submission2)
        .filter(sub1.participation_id == participation1.id, sub1.task_id == task.id)
        .filter(sub2.participation_id == participation2.id)
        .filter(Match.batch == task.pvp_batch)
        .order_by(Match.id.desc())
        .first()
    )
    return last_match

def maybe_send_notification(submission_result):
    """Non-blocking attempt to notify a running SS of the evaluation"""
    ss = RemoteServiceClient(ServiceCoord("ScoringService", 0))
    ss.connect()
    ss.new_evaluation(
        submission_id=submission_result.submission_id,
        dataset_id=submission_result.dataset_id,
    )
    ss.disconnect()


def update_score(session, task, participation, testcase, score, text):
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

    session.commit()

    # maybe_send_notification(match.id) # TODO : update it

def update_final_score(task_name):
    with SessionGen() as session:
        task = session.query(Task).filter(Task.name == task_name).first()
        if not task:
            print("No task called `%s' found." % task_name)
            return False

        matches = (
            session.query(Match)
            .filter(Match.batch == task.pvp_batch)
            .order_by(Match.id.desc())
            .all()
        )

        match_mode = "elo"

        # round-robin
        if match_mode == "round-robin":
            pass  # TODO : update it
            # total_matches = {p.id: 0.0 for p in task.contest.participations}
            # win_matches = {p.id: 0.0 for p in task.contest.participations}
            # for match in matches:
            #     if match.result.get_status() != MatchResult.SCORED:
            #         print("Match %d is not scored." % match.id)
            #         return False
            #     total_matches[match.submission1.participation.id] += 1.0
            #     total_matches[match.submission2.participation.id] += 1.0
            #     win_matches[match.submission1.participation.id] += match.result.score
            #     win_matches[match.submission2.participation.id] += (
            #         1.0 - match.result.score
            #     )

            # for p in task.contest.participations:
            #     if total_matches[p.id] != 0.0:
            #         final_scores[p.id] = win_matches[p.id] / total_matches[p.id]
        # end round-robin

        # elo
        elif match_mode == "elo":
            competition_sys = {
                tc.id: Elo(
                    participation_ids=[p.id for p in task.contest.participations]
                )
                for tc in task.active_dataset.testcases.values()
            }
        else:
            competition_sys = {}

        participations = {}

        for p in task.contest.participations:
            submission = get_match_submission(session, p, task)
            if not submission:
                for tc in task.active_dataset.testcases.values():
                    competition_sys[tc.id].players[p.id] = 0.0
            else:
                participations[p] = submission

        for match in matches:
            if match.result.get_status() != MatchResult.SCORED:
                print("Match %d is not scored." % match.id)
                return False
            for matching in match.result.matchings:
                competition_sys[matching.testcase_id].update_scores(
                    match.submission1.participation_id,
                    match.submission2.participation_id,
                    float(matching.outcome.split()[0].strip()),
                )

        for tc in task.active_dataset.testcases.values():
            sorted_players = sorted(
                competition_sys[tc.id].players.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            all_num = len(sorted_players)
            for rank, (participation_id, score) in enumerate(sorted_players, start=1):
                new_score = 1.0 / rank
                update_score(
                    session,
                    task,
                    session.query(Participation).get(participation_id),
                    tc,
                    new_score,
                    [
                        "Your rating: %s. Rank among all participations: %s/%s.",
                        "%0.2f" % competition_sys[tc.id].players[participation_id],
                        "%d" % rank,
                        "%d" % all_num,
                    ],
                )

        for submission in participations.values():
            result = submission.get_result()
            result.invalidate_score()
            result.set_evaluation_outcome()

        session.commit()

    return True


def main():
    """Parse arguments and launch process."""
    parser = argparse.ArgumentParser(
        description="Update the final score for a PvP task."
    )

    parser.add_argument(
        "task_name", action="store", type=utf8_decoder, help="short name of the task"
    )

    args = parser.parse_args()

    success = update_final_score(task_name=args.task_name)
    return 0 if success is True else 1


if __name__ == "__main__":
    sys.exit(main())
