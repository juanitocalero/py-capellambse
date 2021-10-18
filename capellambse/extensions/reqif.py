# Copyright 2021 DB Netz AG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tools for handling ReqIF Requirements.

.. diagram:: [CDB] Requirements ORM
"""
from __future__ import annotations

import datetime
import itertools
import logging
import typing as t

from lxml import etree

import capellambse.model
import capellambse.model.common as c
from capellambse import helpers
from capellambse.loader import xmltools
from capellambse.model import crosslayer

XT_REQUIREMENT = "Requirements:Requirement"
XT_REQ_ATTR_STRINGVALUE = "Requirements:StringValueAttribute"
XT_REQ_ATTR_REALVALUE = "Requirements:RealValueAttribute"
XT_REQ_ATTR_INTEGERVALUE = "Requirements:IntegerValueAttribute"
XT_REQ_ATTR_DATEVALUE = "Requirements:DateValueAttribute"
XT_REQ_ATTR_BOOLEANVALUE = "Requirements:BooleanValueAttribute"
XT_REQ_ATTR_ENUMVALUE = "Requirements:EnumerationValueAttribute"
XT_REQ_ATTRIBUTES = {
    XT_REQ_ATTR_ENUMVALUE,
    XT_REQ_ATTR_STRINGVALUE,
    XT_REQ_ATTR_REALVALUE,
    XT_REQ_ATTR_INTEGERVALUE,
    XT_REQ_ATTR_DATEVALUE,
    XT_REQ_ATTR_BOOLEANVALUE,
}
XT_INC_RELATION = "CapellaRequirements:CapellaIncomingRelation"
XT_OUT_RELATION = "CapellaRequirements:CapellaOutgoingRelation"
XT_INT_RELATION = "Requirements:InternalRelation"
XT_MODULE = "CapellaRequirements:CapellaModule"
XT_FOLDER = "Requirements:Folder"

XT_REQ_TYPES_F = "CapellaRequirements:CapellaTypesFolder"
XT_REQ_TYPES_DATA_DEF = "Requirements:DataTypeDefinition"
XT_REQ_TYPE = "Requirements:RequirementType"
XT_RELATION_TYPE = "Requirements:RelationType"
XT_MODULE_TYPE = "Requirements:ModuleType"
XT_REQ_TYPE_ENUM = "Requirements:EnumerationDataTypeDefinition"
XT_REQ_TYPE_ATTR_ENUM = "Requirements:EnumValue"
XT_REQ_TYPE_ATTR_DEF = "Requirements:AttributeDefinition"
XT_REQ_TYPE_ENUM_DEF = "Requirements:AttributeDefinitionEnumeration"
XT_REQ_TYPES = {
    XT_REQ_TYPES_F,
    XT_REQ_TYPES_DATA_DEF,
    XT_REQ_TYPE,
    XT_RELATION_TYPE,
    XT_MODULE_TYPE,
    XT_REQ_TYPE_ENUM,
    XT_REQ_TYPE_ATTR_ENUM,
    XT_REQ_TYPE_ATTR_DEF,
    XT_REQ_TYPE_ENUM_DEF,
}
DATE_VALUE_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"

logger = logging.getLogger("reqif")
_xt_to_attr_type: dict[
    str, tuple[t.Callable[[str], t.Any], type | tuple[type, ...], t.Any]
] = {
    XT_REQ_ATTR_STRINGVALUE: (str, str, ""),
    XT_REQ_ATTR_REALVALUE: (float, float, 0.0),
    XT_REQ_ATTR_INTEGERVALUE: (int, int, 0),
    XT_REQ_ATTR_DATEVALUE: (
        lambda val: datetime.datetime.strptime(val, DATE_VALUE_FORMAT),
        (datetime.datetime, type(None)),
        None,
    ),
    XT_REQ_ATTR_BOOLEANVALUE: (lambda val: val == "true", bool, False),
}
_attr_type_hints = {
    "int": XT_REQ_ATTR_INTEGERVALUE,
    "integer": XT_REQ_ATTR_INTEGERVALUE,
    "integervalueattribute": XT_REQ_ATTR_INTEGERVALUE,
    "str": XT_REQ_ATTR_STRINGVALUE,
    "string": XT_REQ_ATTR_STRINGVALUE,
    "stringvalueattribute": XT_REQ_ATTR_STRINGVALUE,
    "float": XT_REQ_ATTR_REALVALUE,
    "real": XT_REQ_ATTR_REALVALUE,
    "realvalueattribute": XT_REQ_ATTR_REALVALUE,
    "date": XT_REQ_ATTR_DATEVALUE,
    "datevalueattribute": XT_REQ_ATTR_DATEVALUE,
    "bool": XT_REQ_ATTR_BOOLEANVALUE,
    "boolvalueattribute": XT_REQ_ATTR_BOOLEANVALUE,
    "enum": XT_REQ_ATTR_ENUMVALUE,
    "enumvalueattribute": XT_REQ_ATTR_ENUMVALUE,
}


class RequirementsRelationAccessor(
    c.WritableAccessor["AbstractRequirementsRelation"]
):
    """Searches for requirement relations in the architecture layer."""

    # pylint: disable=abstract-method  # Only partially implemented for now

    __slots__ = ("aslist",)

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw, aslist=c.ElementList)

    def __get__(self, obj, objtype=None):
        del objtype
        if obj is None:  # pragma: no cover
            return self

        rel_objs = list(
            obj._model._loader.iterchildren_xt(obj._element, XT_INC_RELATION)
        )

        for i in obj._model._loader.iterall_xt(XT_OUT_RELATION):
            if RequirementsOutRelation.from_model(obj._model, i).source == obj:
                rel_objs.append(i)

        for i in obj._model._loader.iterall_xt(XT_INT_RELATION):
            rel = RequirementsIntRelation.from_model(obj._model, i)
            if obj in (rel.source, rel.target):
                rel_objs.append(i)

        assert self.aslist is not None
        return self.aslist(obj._model, rel_objs, c.GenericElement, parent=obj)

    def create(
        self,
        elmlist: c.ElementListCouplingMixin,
        /,
        *type_hints: t.Optional[str],
        **kw: t.Any,
    ) -> c.T:
        if "target" not in kw:
            raise TypeError("No `target` for new requirement relation")
        cls: t.Type[c.T]
        cls, xtype = self._find_relation_type(kw["target"])
        parent = elmlist._parent._element
        with elmlist._model._loader.new_uuid(parent) as uuid:
            return cls(
                elmlist._model,
                parent,
                **kw,
                source=elmlist._parent,
                uuid=uuid,
                xtype=xtype,
            )

    def _find_relation_type(
        self, target: c.GenericElement
    ) -> t.Tuple[t.Type[c.T], str]:
        if isinstance(target, Requirement):
            return (
                t.cast(t.Type[c.T], RequirementsIntRelation),
                XT_INT_RELATION,
            )
        elif isinstance(target, ReqIFElement):
            raise TypeError(
                "Cannot create relations to targets of type"
                f" {type(target).__name__}"
            )
        else:
            return (
                t.cast(t.Type[c.T], RequirementsIncRelation),
                XT_INC_RELATION,
            )


class ElementRelationAccessor(
    c.WritableAccessor["AbstractRequirementsRelation"]
):
    """Provides access to RequirementsRelations of a GenericElement."""

    # pylint: disable=abstract-method  # Only partially implemented for now

    __slots__ = ("aslist",)

    def __init__(self) -> None:
        super().__init__(aslist=RelationsList)

    def __get__(self, obj, objtype=None):
        del objtype
        if obj is None:  # pragma: no cover
            return self

        loader = obj._model._loader
        layertypes = list(filter(None, c.XTYPE_HANDLERS.keys()))
        assert layertypes
        syseng = next(
            loader.iterchildren_xt(
                obj._model._element, capellambse.model.XT_SYSENG
            ),
            None,
        )
        layers = loader.iterchildren_xt(syseng, *layertypes)
        modules = itertools.chain.from_iterable(
            loader.iterchildren_xt(i, XT_MODULE) for i in layers
        )
        inc_int_relations = itertools.chain.from_iterable(
            loader.iterdescendants_xt(i, XT_INC_RELATION, XT_INT_RELATION)
            for i in modules
        )
        out_relations = loader.iterchildren_xt(obj._element, XT_OUT_RELATION)

        def targetof(i: etree._Element) -> c.GenericElement:
            rel = c.GenericElement.from_model(obj._model, i)
            assert isinstance(
                rel,
                (
                    RequirementsOutRelation,
                    RequirementsIncRelation,
                    RequirementsIntRelation,
                ),
            )
            return rel.target

        assert self.aslist is not None
        return self.aslist(
            obj._model,
            list(
                itertools.chain(
                    (i for i in inc_int_relations if targetof(i) == obj),
                    (i for i in out_relations if targetof(i) == obj),
                )
            ),
            None,
            parent=obj,
            side="source",
        )

    def insert(
        self,
        elmlist: c.ElementListCouplingMixin,
        index: int,
        value: c.ModelObject,
    ) -> None:
        if not isinstance(value, Requirement):
            raise TypeError("`value` must be of type 'Requirement'")

        cls = t.cast(t.Type[c.T], RequirementsOutRelation)
        parent = elmlist._parent
        with parent._model._loader.new_uuid(parent._element) as uuid:
            relation = cls(
                elmlist._model,
                parent._element,
                source=value,
                target=elmlist._parent,
                uuid=uuid,
                xtype=XT_OUT_RELATION,
            )
            xml_index = 0
            parent._element.remove(relation._element)
            for elt in parent._element.iterchildren():
                if helpers.xtype_of(elt) == XT_OUT_RELATION:
                    if index == 0:
                        break
                    index -= 1

                xml_index += 1

            parent._element.insert(xml_index, relation._element)

    def delete(
        self, elmlist: c.ElementListCouplingMixin, obj: Requirement
    ) -> None:
        try:
            index = elmlist.outgoing.index(obj)
            element: etree._Element = elmlist._parent._element
            relations = list(
                obj._model._loader.iterchildren_xt(element, XT_OUT_RELATION)
            )
        except ValueError:
            index = index = elmlist.incoming.index(obj)
            element = obj._element
            relations = list(
                obj._model._loader.iterchildren_xt(element, XT_INC_RELATION)
            )
        element.remove(relations[index])


class ReqIFElement(c.GenericElement):
    """Attributes shared by all ReqIF elements."""

    identifier = xmltools.AttributeProperty(
        "_element", "ReqIFIdentifier", optional=True
    )
    long_name = xmltools.AttributeProperty(
        "_element", "ReqIFLongName", optional=True
    )
    description = xmltools.AttributeProperty(
        "_element", "ReqIFDescription", optional=True
    )
    name = xmltools.AttributeProperty("_element", "ReqIFName", optional=True)
    prefix = xmltools.AttributeProperty(
        "_element", "ReqIFPrefix", optional=True
    )
    type = property(lambda _: None)

    def __repr__(self) -> str:  # pragma: no cover
        mytype = type(self).__name__
        path = []
        parent = self._element
        if isinstance(
            self,
            (
                RequirementsOutRelation,
                RequirementsIncRelation,
                RequirementsIntRelation,
            ),
        ):
            return (
                f"<{mytype} from {self.source!r} to {self.target!r} "
                f"({self.uuid})>"
            )
        elif self.xtype in XT_REQ_TYPES:
            return f'<{mytype} {parent.get("ReqIFLongName")!r} ({self.uuid})>'
        while parent is not None:
            path.append(
                parent.get("ReqIFText")
                or parent.get("ReqIFName")
                or parent.get("ReqIFChapterName")
                or parent.get("ReqIFLongName")
                or "..."
            )
            if helpers.xtype_of(parent) == XT_MODULE:
                break
            parent = parent.getparent()

        return f'<{mytype} {"/".join(reversed(path))!r} ({self.uuid})>'


@c.xtype_handler(None, XT_REQ_TYPES_DATA_DEF)
class DataTypeDefinition(ReqIFElement):
    """A data type definition for requirement types"""

    _xmltag = "ownedDefinitionTypes"


@c.xtype_handler(None, XT_REQ_TYPE_ATTR_DEF)
class AttributeDefinition(ReqIFElement):
    """An attribute definition for requirement types"""

    _xmltag = "ownedAttributes"

    data_type = c.AttrProxyAccessor(DataTypeDefinition, "definitionType")


class AbstractRequirementsAttribute(c.GenericElement):
    _xmltag = "ownedAttributes"

    def __repr__(self) -> str:
        mytype = self.xtype.rsplit(":", maxsplit=1)[-1]
        try:
            name = self.definition.long_name
        except AttributeError:
            name = ""
        return f"<{mytype} [{name}] ({self.uuid})>"


class AttributeAccessor(
    c.ProxyAccessor["RequirementsAttribute | EnumerationValueAttribute"]
):
    def __init__(self) -> None:
        super().__init__(
            c.GenericElement, XT_REQ_ATTRIBUTES, aslist=c.MixedElementList
        )

    def _match_xtype(self, type_: str) -> tuple[type, str]:
        type_ = type_.lower()
        try:
            xt = _attr_type_hints[type_]
        except KeyError:
            raise ValueError(f"Invalid type hint given: {type_!r}") from None

        if xt == XT_REQ_ATTR_ENUMVALUE:
            return EnumerationValueAttribute, xt

        return RequirementsAttribute, xt


class RelationsList(c.ElementList["AbstractRequirementsRelation"]):
    def __init__(
        self,
        model: capellambse.MelodyModel,
        elements: t.List[etree._Element],
        elemclass: t.Type[t.Any] = None,
        *,
        side: str = "source",
    ) -> None:
        del elemclass
        assert side in {"source", "target"}
        super().__init__(model, elements, c.GenericElement)
        self._side = side

    def __getitem__(self, idx: int | slice) -> c.GenericElement:
        if isinstance(idx, slice):
            return self._newlist(self._elements[idx])
        return getattr(
            c.GenericElement.from_model(self._model, self._elements[idx]),
            self._side,
        )

    def by_relation_type(self, reltype: str) -> RelationsList:
        matches = []
        for elm in self._elements:
            rel_elm = c.GenericElement.from_model(self._model, elm)
            assert isinstance(
                rel_elm,
                (
                    RequirementsIncRelation,
                    RequirementsOutRelation,
                    RequirementsIntRelation,
                ),
            )
            if rel_elm.type is not None and rel_elm.type.name == reltype:
                matches.append(elm)
        return self._newlist(matches)

    @property
    def incoming(self) -> RelationsList:
        return self._filter_by_relcls(RequirementsIncRelation)

    @property
    def outgoing(self) -> RelationsList:
        return self._filter_by_relcls(RequirementsOutRelation)

    def _newlist(self, elements: t.List[etree._Element]) -> RelationsList:
        listtype = self._newlist_type()
        assert issubclass(listtype, RelationsList)
        return listtype(self._model, elements, side=self._side)

    def _filter_by_relcls(
        self,
        relcls: t.Type[
            RequirementsIncRelation
            | RequirementsOutRelation
            | RequirementsIntRelation
        ],
    ):
        matches = [
            rel_elm._element
            for elm in self._elements
            if isinstance(
                rel_elm := c.GenericElement.from_model(self._model, elm),
                relcls,
            )
        ]
        return self._newlist(matches)


class ValueAccessor(c.Accessor):
    def __get__(
        self,
        obj: RequirementsAttribute | EnumerationValueAttribute,
        objtype: t.Optional[type[ReqIFElement]] = None,
    ) -> ValueAccessor | int | float | str | bool | datetime.datetime:
        del objtype
        cast, _, default = _xt_to_attr_type[obj.xtype]
        if (value := obj._element.get("value")) is None:
            return default
        return cast(value)

    def __set__(
        self,
        obj: RequirementsAttribute | EnumerationValueAttribute,
        value: int | float | str | bool | datetime.datetime,
    ) -> None:
        _, type_, default = _xt_to_attr_type[obj.xtype]
        if not isinstance(value, type_):
            if isinstance(type_, tuple):
                error_msg = " or ".join((t.__name__ for t in type_))
            else:
                error_msg = type_.__name__

            raise TypeError("Value needs to be of type " + error_msg)

        if value == default:
            del obj._element.attrib["value"]
            return

        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, datetime.datetime):
            value = datetime.datetime.strftime(value, DATE_VALUE_FORMAT)
        else:
            value = str(value)

        obj._element.attrib["value"] = value


@c.xtype_handler(None, *(XT_REQ_ATTRIBUTES - {XT_REQ_ATTR_ENUMVALUE}))
class RequirementsAttribute(AbstractRequirementsAttribute):
    """Handles attributes on Requirements."""

    definition = c.AttrProxyAccessor(AttributeDefinition, "definition")

    value = ValueAccessor()


@c.xtype_handler(None, XT_REQ_TYPE_ATTR_ENUM)
class EnumValue(ReqIFElement):
    """An enumeration value for :class:`EnumDataTypeDefinition`"""

    _xmltag = "specifiedValues"


@c.xtype_handler(None, XT_REQ_TYPE_ENUM)
class EnumDataTypeDefinition(ReqIFElement):
    """An enumeration data type definition for requirement types"""

    _xmltag = "ownedDefinitionTypes"

    values = c.ProxyAccessor(
        EnumValue, XT_REQ_TYPE_ATTR_ENUM, aslist=c.ElementList
    )


@c.xtype_handler(None, XT_REQ_TYPE_ENUM_DEF)
class AttributeDefinitionEnumeration(ReqIFElement):
    """An enumeration attribute definition for requirement types"""

    _xmltag = "enumeration"

    data_type = c.AttrProxyAccessor(EnumDataTypeDefinition, "definitionType")
    multi_valued = xmltools.AttributeProperty(
        "_element",
        "multiValued",
        optional=True,
        default=False,
        returntype=bool,
    )


@c.xtype_handler(None, XT_REQ_ATTR_ENUMVALUE)
class EnumerationValueAttribute(AbstractRequirementsAttribute):
    """An enumeration attribute."""

    definition = c.AttrProxyAccessor(
        AttributeDefinitionEnumeration, "definition"
    )
    values = c.AttrProxyAccessor(EnumValue, "values", aslist=c.ElementList)


class AbstractType(ReqIFElement):
    owner = c.ParentAccessor(c.GenericElement)
    attribute_definitions = c.ProxyAccessor(
        c.GenericElement,
        (XT_REQ_TYPE_ATTR_DEF, XT_REQ_TYPE_ENUM_DEF),
        aslist=c.MixedElementList,
    )


@c.xtype_handler(None, XT_MODULE_TYPE)
class ModuleType(AbstractType):
    """A requirement-module type"""

    _xmltag = "ownedTypes"


@c.xtype_handler(None, XT_RELATION_TYPE)
class RelationType(AbstractType):
    """A requirement-relation type"""

    _xmltag = "ownedTypes"


@c.xtype_handler(None, XT_REQ_TYPE)
class RequirementType(AbstractType):
    """A requirement type"""

    _xmltag = "ownedTypes"


@c.xtype_handler(None, XT_REQUIREMENT)
class Requirement(ReqIFElement):
    """A ReqIF Requirement."""

    _xmltag = "ownedRequirements"

    chapter_name = xmltools.AttributeProperty(
        "_element", "ReqIFChapterName", optional=True
    )
    foreign_id = xmltools.AttributeProperty(
        "_element", "ReqIFForeignID", optional=True, returntype=int
    )
    text = xmltools.AttributeProperty(
        "_element", "ReqIFText", optional=True, returntype=c.markuptype
    )
    attributes = AttributeAccessor()
    relations = RequirementsRelationAccessor()
    type = c.AttrProxyAccessor(RequirementType, "requirementType")


@c.xtype_handler(None, XT_FOLDER)
class RequirementsFolder(Requirement):
    """A folder that stores Requirements."""

    _xmltag = "ownedRequirements"

    folders: c.Accessor
    requirements = c.ProxyAccessor(
        Requirement, XT_REQUIREMENT, aslist=c.ElementList
    )


@c.xtype_handler(None, XT_MODULE)
class RequirementsModule(ReqIFElement):
    """A ReqIF Module that bundles multiple Requirement folders."""

    _xmltag = "ownedExtensions"

    folders = c.ProxyAccessor(
        RequirementsFolder, XT_FOLDER, aslist=c.ElementList
    )
    requirements = c.ProxyAccessor(
        Requirement, XT_REQUIREMENT, aslist=c.ElementList
    )
    type = c.AttrProxyAccessor(ModuleType, "moduleType")
    attributes = AttributeAccessor()


class AbstractRequirementsRelation(ReqIFElement):
    _required_attrs = frozenset({"source", "target"})

    type = c.AttrProxyAccessor(RelationType, "relationType")
    source = c.AttrProxyAccessor(Requirement, "source")
    target = c.AttrProxyAccessor(c.GenericElement, "target")


@c.xtype_handler(None, XT_OUT_RELATION)
class RequirementsOutRelation(AbstractRequirementsRelation):
    """A Relation between an object and a requirement."""

    _xmltag = "ownedExtensions"

    source = c.AttrProxyAccessor(Requirement, "target")
    target = c.AttrProxyAccessor(c.GenericElement, "source")


@c.xtype_handler(None, XT_INC_RELATION)
class RequirementsIncRelation(AbstractRequirementsRelation):
    """A Relation between a requirement and an object."""

    _xmltag = "ownedRelations"


@c.xtype_handler(None, XT_INT_RELATION)
class RequirementsIntRelation(AbstractRequirementsRelation):
    """A Relation between two requirements."""

    _xmltag = "ownedRelations"


@c.xtype_handler(None, XT_REQ_TYPES_F)
class RequirementsTypesFolder(ReqIFElement):
    _xmltag = "ownedExtensions"

    data_type_definitions = c.ProxyAccessor(
        c.GenericElement,
        (XT_REQ_TYPES_DATA_DEF, XT_REQ_TYPE_ENUM),
        aslist=c.MixedElementList,
    )
    module_types = c.ProxyAccessor(
        ModuleType, XT_MODULE_TYPE, aslist=c.ElementList
    )
    relation_types = c.ProxyAccessor(
        RelationType, XT_RELATION_TYPE, aslist=c.ElementList
    )
    requirement_types = c.ProxyAccessor(
        RequirementType, XT_REQ_TYPE, aslist=c.ElementList
    )


def init() -> None:
    c.set_accessor(
        RequirementsFolder,
        "folders",
        c.ProxyAccessor(RequirementsFolder, XT_FOLDER, aslist=c.ElementList),
    )
    c.set_accessor(c.GenericElement, "requirements", ElementRelationAccessor())
    c.set_accessor(
        crosslayer.BaseArchitectureLayer,
        "requirement_modules",
        c.ProxyAccessor(RequirementsModule, XT_MODULE, aslist=c.ElementList),
    )
    c.set_accessor(
        crosslayer.BaseArchitectureLayer,
        "all_requirements",
        c.ProxyAccessor(
            Requirement,
            XT_REQUIREMENT,
            aslist=c.ElementList,
            rootelem=XT_MODULE,
            deep=True,
        ),
    )
    c.set_accessor(
        crosslayer.BaseArchitectureLayer,
        "requirement_types_folders",
        c.ProxyAccessor(
            RequirementsTypesFolder,
            XT_REQ_TYPES_F,
            aslist=c.ElementList,
        ),
    )
    c.set_accessor(
        crosslayer.BaseArchitectureLayer,
        "all_requirement_types",
        c.ProxyAccessor(
            RequirementType,
            XT_REQ_TYPE,
            aslist=c.ElementList,
            rootelem=XT_REQ_TYPES_F,
            deep=True,
        ),
    )
    c.set_accessor(
        crosslayer.BaseArchitectureLayer,
        "all_module_types",
        c.ProxyAccessor(
            ModuleType,
            XT_MODULE_TYPE,
            aslist=c.ElementList,
            rootelem=XT_REQ_TYPES_F,
            deep=True,
        ),
    )
    c.set_accessor(
        crosslayer.BaseArchitectureLayer,
        "all_relation_types",
        c.ProxyAccessor(
            RelationType,
            XT_RELATION_TYPE,
            aslist=c.ElementList,
            rootelem=XT_REQ_TYPES_F,
            deep=True,
        ),
    )
