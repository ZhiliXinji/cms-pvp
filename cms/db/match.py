#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
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

"""Submission-related database interface for SQLAlchemy."""

from sqlalchemy import Boolean
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship, Session
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.schema import Column, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.types import Integer, Float, String, Unicode, DateTime, Enum, BigInteger
from sqlalchemy.event import listens_for
from sqlalchemy.sql import insert

from cmscommon.datetime import make_datetime
from . import (
    Filename,
    FilenameSchema,
    Digest,
    Base,
    Participation,
    Task,
    Submission,
    Dataset,
    Testcase,
    SessionGen,
)


class Match(Base):
    """Class to store a match."""

    __tablename__ = "matches"

    # Auto increment primary key.
    id = Column(Integer, primary_key=True)

    submission1_id = Column(
        Integer,
        ForeignKey(Submission.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    submission1 = relationship(
        Submission,
        foreign_keys=[submission1_id],
        uselist=False,
    )

    submission2_id = Column(
        Integer,
        ForeignKey(Submission.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    submission2 = relationship(
        Submission,
        foreign_keys=[submission2_id],
        uselist=False,
    )

    result = relationship(
        "MatchResult",
        back_populates="match",
        uselist=False,
        cascade="all, delete-orphan",
    )

    batch = Column(Integer, nullable=True)

class MatchResult(Base):
    """Class to store a match result."""

    EVALUATING = 1
    SCORING = 2
    SCORED = 3

    __tablename__ = "match_results"

    # Auto increment primary key.
    id = Column(Integer, primary_key=True)

    match_id = Column(
        Integer,
        ForeignKey(Match.id, onupdate="CASCADE", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    match = relationship("Match", back_populates="result", uselist=False)

    evaluation_outcome = Column(Enum("ok", name="evaluation_outcome"), nullable=True)

    # 1.0 for submission1 win, 0.5 for draw, 0.0 for submission2 win
    score = Column(Float, nullable=True)

    def evaluated(self):
        """Return whether the submission result has been evaluated.

        return (bool): True if evaluated, False otherwise.

        """
        return self.evaluation_outcome is not None

    def scored(self):
        """Return whether the submission result has been scored.

        return (bool): True if scored, False otherwise.

        """
        return self.score is not None

    def get_status(self):
        """Return the status of this object."""
        if not self.evaluated():
            return MatchResult.EVALUATING
        elif not self.scored():
            return MatchResult.SCORING
        else:
            return MatchResult.SCORED


@listens_for(Match, "after_insert")
def create_match_result(mapper, connection, target):
    connection.execute(insert(MatchResult).values(match_id=target.id))


class TaskFinalScore(Base):
    """Class to store the final score of a task."""

    """Only for PvP tasks."""

    __tablename__ = "task_final_scores"

    task_id = Column(
        Integer,
        ForeignKey(Task.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
        primary_key=True,
    )
    task = relationship(
        Task,
        foreign_keys=[task_id],
        uselist=False,
    )

    participation_id = Column(
        Integer,
        ForeignKey(Participation.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
        primary_key=True,
    )
    participation = relationship(
        Participation,
        foreign_keys=[participation_id],
        uselist=False,
    )

    score = Column(Float, nullable=False, default=0.0)

    win_matches = Column(Float, nullable=False, default=0)
    total_matches = Column(Float, nullable=False, default=0)

    # Unique constraint.
    __table_args__ = (UniqueConstraint("task_id", "participation_id"),)


@listens_for(Task, "after_insert")
def create_task_final_scores(mapper, connection, target):
    """Automatically create TaskFinalScore for all Tasks when a Task is inserted"""
    # TODO: Check if this is a PvP task
    participation_ids = connection.execute("SELECT id FROM participations").fetchall()

    for (participation_id,) in participation_ids:
        connection.execute(
            insert(TaskFinalScore).values(
                task_id=target.id, participation_id=participation_id
            )
        )


@listens_for(Participation, "after_insert")
def create_participation_final_scores(mapper, connection, target):
    """Automatically create TaskFinalScore for all Tasks when a Participation is inserted"""
    task_ids = connection.execute("SELECT id FROM tasks").fetchall()

    for (task_id,) in task_ids:
        # TODO: Check if this is a PvP task

        connection.execute(
            insert(TaskFinalScore).values(task_id=task_id, participation_id=target.id)
        )
