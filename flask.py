# -*- coding: utf-8 -*-
from __future__ import with_statement
import os
import sys

from threading import local
from jinja2 import Environment, PackageLoader, FileSystemLoader
from werkzeug import Request as RequestBase, Response as ResponseBase, \
     LocalStack, LocalProxy, create_environ, cached_property, \
     SharedDataMiddleware
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import HTTPException, InternalServerError
from werkzeug.contrib.securecookie import SecureCookie

from werkzeug import abort, redirect
from jinja2 import Markup, escape

try:
    import pkg_resources
    pkg_resources.resource_stream
except (ImportError, AttributeError):
    pkg_resources = None


class Request(RequestBase):

    def __init__(self, environ):
        RequestBase.__init__(self, environ)
        self.endpoint = None
        self.view_args = None


class Response(ResponseBase):
    default_mimetype = 'text/html'


class _RequestGlobals(object):
    pass


class _RequestContext(object):

    def __init__(self, app, environ):
        self.app = app
        self.url_adapter = app.url_map.bind_to_environ(environ)
        self.request = app.request_class(environ)
        self.session = app.open_session(self.request)
        self.g = _RequestGlobals()
        self.flashes = None

    def __enter__(self):
        _request_ctx_stack.push(self)

    def __exit__(self, exc_type, exc_value, tb):
        if tb is None or not self.app.debug:
            _request_ctx_stack.pop()


def url_for(endpoint, **values):
    return _request_ctx_stack.top.url_adapter.build(endpoint, values)


def flash(message):
    session['_flashes'] = (session.get('_flashes', [])) + [message]


def get_flashed_messages():
    flashes = _request_ctx_stack.top.flashes
    if flashes is None:
        _request_ctx_stack.top.flashes = flashes = \
            session.pop('_flashes', [])
    return flashes


def render_template(template_name, **context):
    current_app.update_template_context(context)
    return current_app.jinja_env.get_template(template_name).render(context)


def render_template_string(source, **context):
    current_app.update_template_context(context)
    return current_app.jinja_env.from_string(source).render(context)


def _default_template_ctx_processor():
    reqctx = _request_ctx_stack.top
    return dict(
        request=reqctx.request,
        session=reqctx.session,
        g=reqctx.g
    )


def _get_package_path(name):
    try:
        return os.path.abspath(os.path.dirname(sys.modules[name].__file__))
    except (KeyError, AttributeError):
        return os.getcwd()


class Flask(object):
    request_class = Request

    response_class = Response

    static_path = '/static'

    secret_key = None

    session_cookie_name = 'session'

    jinja_options = dict(
        autoescape=True,
        extensions=['jinja2.ext.autoescape', 'jinja2.ext.with_']
    )

    def __init__(self, package_name):
        self.debug = False

        self.package_name = package_name

        self.root_path = _get_package_path(self.package_name)

        self.view_functions = {}

        self.error_handlers = {}

        self.before_request_funcs = []

        self.after_request_funcs = []

        self.template_context_processors = [_default_template_ctx_processor]

        self.url_map = Map()

        if self.static_path is not None:
            self.url_map.add(Rule(self.static_path + '/<filename>',
                                  build_only=True, endpoint='static'))
            if pkg_resources is not None:
                target = (self.package_name, 'static')
            else:
                target = os.path.join(self.root_path, 'static')
            self.wsgi_app = SharedDataMiddleware(self.wsgi_app, {
                self.static_path: target
            })

        self.jinja_env = Environment(loader=self.create_jinja_loader(),
                                     **self.jinja_options)
        self.jinja_env.globals.update(
            url_for=url_for,
            get_flashed_messages=get_flashed_messages
        )

    def create_jinja_loader(self):
        if pkg_resources is None:
            return FileSystemLoader(os.path.join(self.root_path, 'templates'))
        return PackageLoader(self.package_name)

    def update_template_context(self, context):
        reqctx = _request_ctx_stack.top
        for func in self.template_context_processors:
            context.update(func())

    def run(self, host='localhost', port=5000, **options):
        from werkzeug import run_simple
        if 'debug' in options:
            self.debug = options.pop('debug')
        options.setdefault('use_reloader', self.debug)
        options.setdefault('use_debugger', self.debug)
        return run_simple(host, port, self, **options)

    def test_client(self):
        from werkzeug import Client
        return Client(self, self.response_class, use_cookies=True)

    def open_resource(self, resource):
        if pkg_resources is None:
            return open(os.path.join(self.root_path, resource), 'rb')
        return pkg_resources.resource_stream(self.package_name, resource)

    def open_session(self, request):
        key = self.secret_key
        if key is not None:
            return SecureCookie.load_cookie(request, self.session_cookie_name,
                                            secret_key=key)

    def save_session(self, session, response):
        if session is not None:
            session.save_cookie(response, self.session_cookie_name)

    def add_url_rule(self, rule, endpoint, **options):
        options['endpoint'] = endpoint
        options.setdefault('methods', ('GET',))
        self.url_map.add(Rule(rule, **options))

    def route(self, rule, **options):
        def decorator(f):
            self.add_url_rule(rule, f.__name__, **options)
            self.view_functions[f.__name__] = f
            return f
        return decorator

    def errorhandler(self, code):
        def decorator(f):
            self.error_handlers[code] = f
            return f
        return decorator

    def before_request(self, f):
        self.before_request_funcs.append(f)
        return f

    def after_request(self, f):
        self.after_request_funcs.append(f)
        return f

    def context_processor(self, f):
        self.template_context_processors.append(f)
        return f

    def match_request(self):
        rv = _request_ctx_stack.top.url_adapter.match()
        request.endpoint, request.view_args = rv
        return rv

    def dispatch_request(self):
        try:
            endpoint, values = self.match_request()
            return self.view_functions[endpoint](**values)
        except HTTPException, e:
            handler = self.error_handlers.get(e.code)
            if handler is None:
                return e
            return handler(e)
        except Exception, e:
            handler = self.error_handlers.get(500)
            if self.debug or handler is None:
                raise
            return handler(e)

    def make_response(self, rv):
        if isinstance(rv, self.response_class):
            return rv
        if isinstance(rv, basestring):
            return self.response_class(rv)
        if isinstance(rv, tuple):
            return self.response_class(*rv)
        return self.response_class.force_type(rv, request.environ)

    def preprocess_request(self):
        for func in self.before_request_funcs:
            rv = func()
            if rv is not None:
                return rv

    def process_response(self, response):
        session = _request_ctx_stack.top.session
        if session is not None:
            self.save_session(session, response)
        for handler in self.after_request_funcs:
            response = handler(response)
        return response

    def wsgi_app(self, environ, start_response):
        with self.request_context(environ):
            rv = self.preprocess_request()
            if rv is None:
                rv = self.dispatch_request()
            response = self.make_response(rv)
            response = self.process_response(response)
            return response(environ, start_response)

    def request_context(self, environ):
        return _RequestContext(self, environ)

    def test_request_context(self, *args, **kwargs):
        return self.request_context(create_environ(*args, **kwargs))

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)


_request_ctx_stack = LocalStack()
current_app = LocalProxy(lambda: _request_ctx_stack.top.app)
request = LocalProxy(lambda: _request_ctx_stack.top.request)
session = LocalProxy(lambda: _request_ctx_stack.top.session)
g = LocalProxy(lambda: _request_ctx_stack.top.g)
