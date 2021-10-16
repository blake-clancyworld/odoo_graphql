# -*- coding: utf-8 -*-

# https://github.com/graphql-python/graphql-core
from odoo import tools
from odoo.osv.expression import AND
from graphql.language.ast import (
    VariableNode,
    ValueNode,
    ListValueNode,
    IntValueNode,
    FloatValueNode
)


def parse_document(env, doc, variables={}):  # Un document peut avoir plusieurs définitions
    variables = {**env.context, **variables}
    for definition in doc.definitions:
        return parse_definition(env, definition, variables=variables)


def model2name(model):
    return "".join(p.title() for p in model.split("."))


# See self.clear_caches(): we need a cache that changes with module install?
# @tools.ormcache()
def get_model_mapping(env):
    return {
        model2name(name): model
        for name, model in env.items()
    }

def filter_by_directives(node, variables={}):
    if not node.selection_set:
        return
    selections = []
    for field in node.selection_set.selections:
        if parse_directives(field.directives, variables=variables):
            selections.append(field)
            filter_by_directives(field, variables=variables)
    node.selection_set.selections = selections


def parse_directives(directives, variables={}):
    """Currently return True to keep, False to skip """
    for d in directives:
        if d.name.value == "include":
            for arg in d.arguments:
                if arg.name.value == 'if':
                    value = value2py(arg.value, variables=variables)
                    return value
        elif d.name.value == "skip":
            for arg in d.arguments:
                if arg.name.value == 'if':
                    value = value2py(arg.value, variables=variables)
                    return not value
    return True  # Keep by default


def parse_definition(env, d, variables={}):
    type = d.operation.value  # MUTATION OR QUERY
    # name = d.name.value  # Usage in response? Only for debug
    if type != "query":
        return  # Does not support mutations currently

    filter_by_directives(d, variables)

    data = {}
    model_mapping = get_model_mapping(env)
    for field in d.selection_set.selections:
        model = model_mapping[field.name.value]
        fname = field.alias and field.alias.value or field.name.value
        gather = convert_model_field(model, field, variables=variables)
        data[fname] = gather()
    return data


def get_fields_data(model, fields):
    relations = {}
    for field in fields:
        name = field.name.value
        f = model._fields[name]
        r = relations.setdefault(
            name,
            (
                model.env[f.comodel_name] if f.relational else None,
                name,
                [],
            )
        )
        r[2].append(field)
    return relations.values()


OPTIONS = [
    ("offset", int),
    ("limit", int),
    ("order", str)
]


# https://stackoverflow.com/questions/45674423/how-to-filter-greater-than-in-graphql
def parse_arguments(args, variables={}):  # return a domain and kwargs
    args = {
        a.name.value: value2py(a.value, variables)
        for a in args
    }
    kwargs = {}
    for opt, cast in OPTIONS:
        value = args.pop(opt, None)
        if value:
            kwargs[opt] = cast(value)
    return args.pop("domain", []), kwargs


def value2py(value, variables={}):
    if isinstance(value, VariableNode):
        return variables.get(value.name.value)
    if isinstance(value, ValueNode):
        if isinstance(value, ListValueNode):
            return [
                value2py(v, variables=variables)
                for v in value.values
            ]
        # For unknown reason, integers and floats are received as string,
        # but not booleans nor list
        if isinstance(value, IntValueNode):
            return int(value.value)
        if isinstance(value, FloatValueNode):
            return float(value.value)
        return value.value
        
    raise Exception("Can not convert")

def make_domain(domain, ids):
    if ids:
        if isinstance(ids, (list, tuple)):
            domain = AND([
                [("id", "in", ids)],
                domain
            ])
        elif isinstance(ids, int):
            domain = AND([
                [("id", "=", ids)],
                domain
            ])
    return domain


def do_nothing(value):
    return value


def get_aliases(submodel, fields, variables={}):
    aliases = []
    if submodel is not None:
        # If relational field, we want to get the subdatas
        for f in fields:
            alias = f.alias and f.alias.value or f.name.value
            subgather = do_nothing  # If no subdata requested, return the ids
            if f.selection_set:
                subgather = convert_model_field(
                    submodel, f,
                    variables=variables
                )
            aliases.append(
                (alias, subgather)
            )
    else:
        for f in fields:
            alias = f.alias and f.alias.value or f.name.value
            aliases.append(
                (alias, do_nothing)
            )
    return aliases

def get_subgathers(fields_data, variables={}):
    subgathers = {}
    for submodel, fname, fields in fields_data:
        aliases = get_aliases(submodel, fields, variables=variables)
        if aliases:
            subgathers[fname] = aliases
    return subgathers

def convert_model_field(model, field, variables={}):
    domain, kwargs = parse_arguments(field.arguments, variables)
    fields = field.selection_set.selections
    fields_names = [f.name.value for f in fields]
    fields_data = get_fields_data(model, fields)  # [(model, fname, fields), ...]

    subgathers = get_subgathers(fields_data, variables=variables)

    def gather(ids=None):
        records = model.search(
            make_domain(domain, ids), **kwargs
        )
        records = records.read(fields_names, load=False)
        data = []
        for rec in records:
            tmp = {}
            for key, value in rec.items():
                aliases = subgathers.get(key, [])
                for alias, func in aliases:
                    tmp[alias] = func(value)
            data.append(tmp)
        
        if data and isinstance(ids, int):
            data = data[0]
        return data

    return gather