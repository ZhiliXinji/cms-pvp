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

"""Utility to start a match."""

import argparse
import sys
import time
import random
import logging

from cms import utf8_decoder, ServiceCoord
from cms.db import (
    SessionGen,
    Task,
    Submission,
    Participation,
    File,
    Match,
    SubmissionResult,
)
from cmscommon.datetime import make_datetime
from cms.io import RemoteServiceClient

logger = logging.getLogger(__name__)


def maybe_send_notification(match_id):
    """Non-blocking attempt to notify a running ES of the submission"""

    rs = RemoteServiceClient(ServiceCoord("EvaluationService", 0))
    rs.connect()
    logger.info("Sending notification for match %d", match_id)
    rs.new_match(match_id=match_id)
    rs.disconnect()

def get_last_submission(session, participation, task):
    last_submission = (
        session.query(Submission)
        .join(Submission.participation)
        .join(Submission.task)
        .join(Submission.results)
        .filter(Participation.id == participation.id)
        .filter(Task.id == task.id)
        .filter(SubmissionResult.filter_compilation_succeeded())
        .order_by(Submission.timestamp.desc())
        .first()
    )
    return last_submission

def get_match_submission(session, participation, task):
    match_submission = (
        session.query(Submission)
        .join(Submission.participation)
        .join(Submission.task)
        .filter(Participation.id == participation.id)
        .filter(Task.id == task.id)
        .filter(Submission.pvp_batch == task.pvp_batch)
        .first()
    )
    return match_submission

def add_match(session, task, timestamp, p1, p2):
    s1 = get_match_submission(session, p1, task)
    s2 = get_match_submission(session, p2, task)

    if not s1:
        return False
    if not s2:
        return False

    match = Match(
        submission1=s1,
        submission2=s2,
        task=task,
        timestamp=make_datetime(timestamp),
        batch=task.pvp_batch,
    )

    # print("----", match.task)

    session.add(match)
    session.add(match.get_result_or_create())

    session.commit()

    maybe_send_notification(match.id)

    return True

def next_batch(task_name):
    with SessionGen() as session:
        task = session.query(Task).filter(Task.name == task_name).first()
        if not task:
            print("No task called `%s' found." % task_name)
            return False
        task.pvp_batch += 1
        session.commit()
    return True

def mark_match_submissions(task_name):
    """Mark the submissions that will be used in the match."""
    with SessionGen() as session:
        task = session.query(Task).filter(Task.name == task_name).first()
        if not task:
            print("No task called `%s' found." % task_name)
            return False

        for p in task.contest.participations:
            submission = get_last_submission(session, p, task)
            if submission:
                submission.pvp_batch = task.pvp_batch
                result = submission.get_result_or_create()
                result.score = None
                result.score_details = None
                result.public_score = None
                result.public_score_details = None
                result.ranking_score_details = None
        session.commit()
    return True


def start_match(task_name):
    if not next_batch(task_name):
        return False
    if not mark_match_submissions(task_name):
        return False
    with SessionGen() as session:
        task = session.query(Task).filter(Task.name == task_name).first()
        if not task:
            print("No task called `%s' found." % task_name)
            return False

        match_mode = "elo"

        # round-robin
        if match_mode == "round-robin":
            for p1 in task.contest.participations:
                for p2 in task.contest.participations:
                    if p1.id != p2.id:
                        add_match(session, task, time.time(), p1, p2)
        # end round-robin

        # elo
        num_matches = 2  # set it larger to make result more accurate

        if match_mode == "elo":
            for _ in range(num_matches):
                player_a, player_b = random.sample(task.contest.participations, 2)
                add_match(session, task, time.time(), player_a, player_b)
        # end elo

    return True


def main():
    """Parse arguments and launch process."""
    parser = argparse.ArgumentParser(description="Start match for a task.")

    parser.add_argument(
        "task_name", action="store", type=utf8_decoder, help="short name of the task"
    )

    args = parser.parse_args()

    success = start_match(task_name=args.task_name)
    return 0 if success is True else 1


if __name__ == "__main__":
    sys.exit(main())
