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

from cms import utf8_decoder, ServiceCoord
from cms.db import (
    SessionGen,
    Task,
    Submission,
    MatchResult,
    TaskFinalScore,
    Participation,
    File,
    Match,
)
from cmscommon.datetime import make_datetime
from cms.io import RemoteServiceClient
from sqlalchemy.orm import aliased


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


def update_final_score(task_name):
    with SessionGen() as session:
        task = session.query(Task).filter(Task.name == task_name).first()
        if not task:
            print("No task called `%s' found." % task_name)
            return False

        total_matches = {p.id: 0.0 for p in task.contest.participations}
        win_matches = {p.id: 0.0 for p in task.contest.participations}
        final_scores = {p.id: 0.0 for p in task.contest.participations}

        for p1 in task.contest.participations:
            for p2 in task.contest.participations:
                match = get_last_match(session, p1, p2, task)
                if match:
                    if match.result.get_status() != MatchResult.SCORED:
                        print("Match %d is not scored." % match.id)
                        return False
                    total_matches[p1.id] += 1.0
                    total_matches[p2.id] += 1.0
                    win_matches[p1.id] += match.result.score
                    win_matches[p2.id] += 1.0 - match.result.score

        # round-robin
        for p in task.contest.participations:
            if total_matches[p.id] != 0.0:
                final_scores[p.id] = win_matches[p.id] / total_matches[p.id]
        # end round-robin

        for p in task.contest.participations:
            task_final_score = TaskFinalScore.get_from_id(
                (
                    task.id,
                    p.id,
                ),
                session,
            )

            task_final_score.score = final_scores[p.id]
            task_final_score.win_matches = win_matches[p.id]
            task_final_score.total_matches = total_matches[p.id]

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
