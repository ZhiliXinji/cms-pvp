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

from cms import utf8_decoder, ServiceCoord
from cms.db import SessionGen, Task, Submission, Participation, File, Match
from cmscommon.datetime import make_datetime
from cms.io import RemoteServiceClient

# def maybe_send_notification(submission_id):
#     """Non-blocking attempt to notify a running ES of the submission"""
#     rs = RemoteServiceClient(ServiceCoord("EvaluationService", 0))
#     rs.connect()
#     rs.new_submission(submission_id=submission_id)
#     rs.disconnect()

def get_last_submission(session, participation, task):
    last_submission = (
        session.query(Submission)
        .join(Submission.participation)
        .join(Submission.task)
        .filter(Participation.id == participation.id)
        .filter(Task.id == task.id)
        .order_by(Submission.timestamp.desc())
        .first()
    )
    return last_submission


def add_match(session, task, p1, p2):
    s1 = get_last_submission(session, p1, task)
    s2 = get_last_submission(session, p2, task)
    if not s1:
        return False
    if not s2:
        return False

    match = Match(submission1=s1, submission2=s2, batch=task.pvp_batch)

    session.add(match)
    session.commit()

    # maybe_send_notification(match.id) # TODO : update it

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

def start_match(task_name):
    if not next_batch(task_name):
        return False
    with SessionGen() as session:
        task = session.query(Task).filter(Task.name == task_name).first()
        if not task:
            print("No task called `%s' found." % task_name)
            return False

        # round-robin
        for p1 in task.contest.participations:
            for p2 in task.contest.participations:
                if p1.id != p2.id:
                    add_match(session, task, p1, p2)

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
