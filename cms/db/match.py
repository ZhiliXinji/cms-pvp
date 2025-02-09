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
from sqlalchemy.ext.associationproxy import association_proxy
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

    # Task of the match, gotten from submission1.task
    task_id = Column(
        Integer,
        ForeignKey(Task.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task = relationship(
        Task,
        ForeignKey(Submission.task, onupdate="CASCADE", ondelete="CASCADE"),
        foreign_keys=[task_id],
        uselist=False,
    )

    # Store two relevant submission
    submission1_id = Column(
        Integer,
        ForeignKey(Submission.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    submission1 = relationship(
        Submission,
        foreign_keys=[submission1_id, task],
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

    def get_result(self, dataset=None):
        """Return the result associated to a dataset.

        dataset (Dataset|None): the dataset for which the caller wants
            the submission result; if None, the active one is used.

        return (MatchResult|None): the submission result
            associated to this submission and the given dataset, if it
            exists in the database, otherwise None.

        """
        if dataset is not None:
            # Use IDs to avoid triggering a lazy-load query.
            assert self.task_id == dataset.task_id
            dataset_id = dataset.id
        else:
            dataset_id = self.task.active_dataset_id

        return MatchResult.get_from_id((self.id, dataset_id), self.sa_session)

    def get_result_or_create(self, dataset=None):
        """Return and, if necessary, create the result for a dataset.

        dataset (Dataset|None): the dataset for which the caller wants
            the match result; if None, the active one is used.

        return (MatchResult): the match result associated to
            the this match and the given dataset; if it
            does not exists, a new one is created.

        """
        if dataset is None:
            dataset = self.task.active_dataset

        match_result = self.get_result(dataset)

        if match_result is None:
            match_result = MatchResult(match=self, dataset=dataset)

        return match_result


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
    # Number of failures during evaluation.
    evaluation_tries = Column(Integer, nullable=False, default=0)

    # Match details. It's a JSON-like structure containing information
    # about the match.
    # TODO: determine the structure of this field.
    match_details = Column(JSONB, nullable=True)

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

    def evaluated(self):
        """Return whether the submission result has been evaluated.

        return (bool): True if evaluated, False otherwise.

        """
        return self.evaluation_outcome is not None

    def set_evaluation_outcome(self):
        """Set the evaluation outcome (always ok now)."""
        self.evaluation_outcome = "ok"

    def get_status(self):
        """Return the status of this object."""
        if not self.evaluated():
            return MatchResult.EVALUATING
        # elif not self.scored():
        #     return MatchResult.SCORING
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


