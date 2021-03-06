import inspect
import typing

from starlette_api import codecs, exceptions, http
from starlette_api.codecs.negotiation import negotiate_content_type
from starlette_api.components import Component
from starlette_api.routing import Route
from starlette_api.schema import types, validators

ValidatedPathParams = typing.NewType("ValidatedPathParams", dict)
ValidatedQueryParams = typing.NewType("ValidatedQueryParams", dict)
ValidatedRequestData = typing.TypeVar("ValidatedRequestData")


class RequestDataComponent(Component):
    def __init__(self):
        self.codecs = [codecs.JSONCodec(), codecs.URLEncodedCodec(), codecs.MultiPartCodec()]

    def can_handle_parameter(self, parameter: inspect.Parameter):
        return parameter.annotation is http.RequestData

    async def resolve(self, request: http.Request):
        content_type = request.headers.get("Content-Type")

        try:
            codec = negotiate_content_type(self.codecs, content_type)
        except exceptions.NoCodecAvailable:
            raise exceptions.HTTPException(415)

        try:
            return await codec.decode(request)
        except exceptions.ParseError as exc:
            raise exceptions.HTTPException(400, detail=str(exc))


class ValidatePathParamsComponent(Component):
    async def resolve(self, route: Route, path_params: http.PathParams) -> ValidatedPathParams:
        validator = validators.Object(
            properties=[(f.name, f.schema if f.schema else validators.Any()) for f in route.path_fields.values()],
            required=[name for name in route.path_fields.keys()],
        )

        try:
            path_params = validator.validate(path_params, allow_coerce=True)
        except validators.ValidationError as exc:
            raise exceptions.HTTPException(400, detail=exc.detail)
        return ValidatedPathParams(path_params)


class ValidateQueryParamsComponent(Component):
    def resolve(self, route: Route, query_params: http.QueryParams) -> ValidatedQueryParams:
        validator = validators.Object(
            properties=[(f.name, f.schema if f.schema else validators.Any()) for f in route.query_fields.values()],
            required=[f.name for f in route.query_fields.values() if f.required],
        )

        try:
            query_params = validator.validate(query_params, allow_coerce=True)
        except validators.ValidationError as exc:
            raise exceptions.HTTPException(400, detail=exc.detail)
        return ValidatedQueryParams(query_params)


class ValidateRequestDataComponent(Component):
    def can_handle_parameter(self, parameter: inspect.Parameter):
        return parameter.annotation is ValidatedRequestData

    def resolve(self, route: Route, data: http.RequestData):
        if not route.body_field or not route.body_field.schema:
            return data

        validator = route.body_field.schema

        try:
            return validator.validate(data, allow_coerce=True)
        except validators.ValidationError as exc:
            raise exceptions.HTTPException(400, detail=exc.detail)


class PrimitiveParamComponent(Component):
    def can_handle_parameter(self, parameter: inspect.Parameter):
        return parameter.annotation in (str, int, float, bool, parameter.empty)

    def resolve(
        self, parameter: inspect.Parameter, path_params: ValidatedPathParams, query_params: ValidatedQueryParams
    ):
        params = path_params if (parameter.name in path_params) else query_params
        has_default = parameter.default is not parameter.empty
        allow_null = parameter.default is None

        param_validator = {
            parameter.empty: validators.Any(),
            str: validators.String(allow_null=allow_null),
            int: validators.Integer(allow_null=allow_null),
            float: validators.Number(allow_null=allow_null),
            bool: validators.Boolean(allow_null=allow_null),
        }[parameter.annotation]

        validator = validators.Object(
            properties=[(parameter.name, param_validator)], required=[] if has_default else [parameter.name]
        )

        try:
            params = validator.validate(params, allow_coerce=True)
        except validators.ValidationError as exc:
            raise exceptions.HTTPException(400, detail=exc.detail)
        return params.get(parameter.name, parameter.default)


class CompositeParamComponent(Component):
    def can_handle_parameter(self, parameter: inspect.Parameter):
        return issubclass(parameter.annotation, types.Type)

    def resolve(self, parameter: inspect.Parameter, data: ValidatedRequestData):
        try:
            return parameter.annotation(data)
        except validators.ValidationError as exc:
            raise exceptions.HTTPException(400, detail=exc.detail)


VALIDATION_COMPONENTS = (
    RequestDataComponent(),
    ValidatePathParamsComponent(),
    ValidateQueryParamsComponent(),
    ValidateRequestDataComponent(),
    PrimitiveParamComponent(),
    CompositeParamComponent(),
)
