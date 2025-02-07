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
    File,
    Filename,
    FilenameSchema,
    Digest,
    Base,
    Participation,
    Task,
    Executable,
    Submission,
    SubmissionResult,
    Dataset,
    Testcase,
    SessionGen,
)

class Match(Base):
    """Class to store a match between two submissions."""

    __tablename__ = "matches"

    # Auto increment primary key.
    id = Column(Integer, primary_key=True)

    # Store two relevant submission
    submission1_id = Column(
        Integer,
        ForeignKey(Submission.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
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
        nullable=False,
        index=True,
    )
    submission2 = relationship(
        Submission,
        foreign_keys=[submission2_id],
        uselist=False,
    )

    # Task of the match, gotten from submission1.task
    task_id = Column(
        Integer,
        ForeignKey(Task.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task = relationship(
        Task,
        secondary=Submission.__tablename__,
        primaryjoin="and_(Match.submission1_id == Submission.id, Match.submission2_id == Submission.id)",
        secondaryjoin="Task.id == Submission.task_id",
        viewonly=True,
    )

    # Time of the match.
    timestamp = Column(DateTime, nullable=False)

    # Result of the match
    result = relationship(
        "MatchResult",
        back_populates="match",
        uselist=False,
        cascade="all, delete-orphan",
    )

    batch = Column(Integer, nullable=True)

class MatchResult(Base):
    """Class to store a match result.
    TODO: Need to complete.
    """

    # TODO: add COMPILING and COMPILATION_FAILED judgement.
    COMPILING = 1
    COMPILATION_FAILED = 2
    EVALUATING = 3
    SCORING = 4
    SCORED = 5

    __tablename__ = "match_results"
    __table_args__ = (UniqueConstraint("match_id", "dataset_id"),)

    # Primary key is (match_id, dataset_id).
    match_id = Column(
        Integer,
        ForeignKey(Match.id, onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )
    match = relationship(Match, back_populates="result")

    dataset_id = Column(
        Integer,
        ForeignKey(Dataset.id, onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )
    dataset = relationship(Dataset)

    # TODO: Need to complete like submission_result.

    # Score details. It's a JSON-like structure containing information
    # that is given to ScoreType.get_html_details to generate an HTML
    # snippet that is shown on AWS and, if the user used a token, on
    # CWS to display the details of the submission.
    # For example, results for each testcases, subtask, etc.
    score_details = Column(JSONB, nullable=True)

    # Ranking score details. It is a list of strings that are going to
    # be shown in a single row in the table of submission in RWS.
    ranking_score_details = Column(ARRAY(String), nullable=True)

    # These one-to-many relationships are the reversed directions of
    # the ones defined in the "child" classes using foreign keys.

    # TODO: implement this.
    # executables = relationship(
    #     "Executable",
    #     collection_class=attribute_mapped_collection("filename"),
    #     cascade="all, delete-orphan",
    #     passive_deletes=True,
    #     back_populates="match_result")

    matchings = relationship(
        "Matching",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="result",
    )

    evaluation_outcome = Column(Enum("ok", name="evaluation_outcome"), nullable=True)

    # Performance points of both sides, inside [0.0, 1.0].
    # XXX: Will be changed to an array when not just PvP, like 5v5.
    score1 = Column(Float, nullable=True)
    score2 = Column(Float, nullable=True)

    def evaluated(self):
        """Return whether the submission result has been evaluated.

        return (bool): True if evaluated, False otherwise.

        """
        return self.evaluation_outcome is not None

    def scored(self):
        """Return whether the submission result has been scored.

        return (bool): True if scored, False otherwise.

        """
        return self.score1 is not None or self.score2 is not None

    def get_status(self):
        """Return the status of this object."""
        if not self.evaluated():
            return MatchResult.EVALUATING
        elif not self.scored():
            return MatchResult.SCORING
        else:
            return MatchResult.SCORED

class Matching(Base):
    """Class to store a matching against one testcase.
    TODO: add parameters around evaluation stats.
    """

    __tablename__ = "matchings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("match_id", "dataset_id"),
            (
                MatchResult.match_id,
                MatchResult.dataset_id,
            ),
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        UniqueConstraint("match_id", "dataset_id", "testcase_id"),
    )

    # Auto increment primary key.
    id = Column(Integer, primary_key=True)

    # Match (id and object) owning the matching.
    match_id = Column(
        Integer,
        ForeignKey(Match.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match = relationship(Match, viewonly=True)

    # Dataset (id and object) owning the matching.
    dataset_id = Column(
        Integer,
        ForeignKey(Dataset.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset = relationship(Dataset, viewonly=True)

    # MatchResult owning the matching.
    result = relationship(MatchResult, back_populates="matchings")

    # Testcase (id and object) this matching was performed on.
    testcase_id = Column(
        Integer,
        ForeignKey(Testcase.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    testcase = relationship(Testcase)

    # String containing the outcome of the matching. (usually 1.0 1.0,
    # ... where first two float numbers infer performance points of both sides)
    outcome = Column(Unicode, nullable=True)

    # The output from the grader, usually "Correct", "Time limit", ...
    # (to allow localization the first item of the list is a format
    # string, possibly containing some "%s", that will be filled in
    # using the remaining items of the list).
    text = Column(ARRAY(String), nullable=False, default=[])

    @property
    def codename(self):
        """Return the codename of the testcase."""
        return self.testcase.codename

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
