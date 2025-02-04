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

"""Match-related database interface for SQLAlchemy."""

from sqlalchemy import Boolean
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.schema import Column, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.types import Integer, Float, String, Unicode, DateTime, Enum, BigInteger

from cmscommon.datetime import make_datetime
from . import (
    Filename,
    FilenameSchema,
    Digest,
    Base,
    Participation,
    Task,
    Dataset,
    Testcase,
    Submission,
)


class Match(Submission):
    """
    Match, a special kind of submission
    submit by the system itself
    to compare the submission with another submission
    """

    __mapper_args__ = {
        "polymorphic_identity": "match",
    }

    opponent_id = Column(
        Integer,
        ForeignKey(Submission.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opponent = relationship(
        Submission,
    )
