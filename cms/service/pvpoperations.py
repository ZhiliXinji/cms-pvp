#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
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

"""The ScoringService operation class, and related functions to
compute sets of operations to do.

"""

import logging

from cms.db import Dataset, Submission, SubmissionResult, \
    Task, Participation, Match, Contest, Batch
from cms.io import QueueItem
from sqlalchemy.orm import aliased


logger = logging.getLogger(__name__)

def get_operations(session, timestamp):
    """Return all the operations to do for PvP problems.

    session (Session): the database session to use.
    timestamp (datetime): the timestamp up to which we want to
        compute the operations.

    return ([PvPOperation, float]): a list of operations and
        timestamps.

    """

    # auto batch eval
    # get current running contest
    contests = session.query(Contest).filter(Contest.start <= timestamp).\
        filter(Contest.stop >= timestamp).all()
    for contest in contests:
        for task in contest.tasks:
            if task.active_dataset.task_type == "PvP":
                task_type_object = task.active_dataset.task_type_object
                if task_type_object.auto_eval != "enabled":
                    continue
                last_time = contest.start if task.pvp_batch == 0 \
                    else session.query(Batch).filter(Batch.task_id == task.id).\
                    order_by(Batch.timestamp.desc()).first().timestamp
                if timestamp - last_time >= task_type_object.interval:
                    new_batch = Batch(
                        task=task,
                        timestamp=timestamp,
                        rounds=task_type_object.rounds,
                        matches=[],
                        task_pvp_batch=task.pvp_batch + 1,
                    )
                    session.add(new_batch)
                    session.commit()
                    yield PvPOperation(new_batch.id), timestamp

    # TODO: other batch
    session.commit()

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


class PvPOperation(QueueItem):
    """The operation for the PvP service executor.

    It represent the operation of batch evaluations and update score
    of all participations according to some methods.
    """

    def __init__(self, batch_id):
        self.batch_id = batch_id

    @staticmethod
    def from_dict(d):
        return PvPOperation(d["batch_id"])

    def __eq__(self, other):
        if self.__class__ != other.__class__:
            return False
        return self.batch_id == other.batch_id

    def __hash__(self):
        return hash(self.batch_id)

    def __str__(self):
        return "running new batch %d" % (
            self.batch_id)

    def to_dict(self):
        return {"batch_id": self.batch_id}
