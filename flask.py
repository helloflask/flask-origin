# -*- coding: utf-8 -*-
"""
    flask
    ~~~~~

    A microframework based on Werkzeug.  It's extensively documented
    and follows best practice patterns.

    :copyright: (c) 2010 by Armin Ronacher.
    :license: BSD, see LICENSE for more details.
"""
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

# 这些从Werkzeug和Jinja2导入的辅助函数（utilities）没有在
# 模块内使用，而是直接作为外部接口开放
# utilities we import from Werkzeug and Jinja2 that are unused
# in the module but are exported as public interface.
from werkzeug import abort, redirect
from jinja2 import Markup, escape

# 优先使用pkg_resource，如果无法工作则使用cwd。由于Google App Engine中的
# 著名异常，当前工作目录通常不可靠。
# use pkg_resource if that works, otherwise fall back to cwd.  The
# current working directory is generally not reliable with the notable
# exception of google appengine.
try:
    import pkg_resources
    pkg_resources.resource_stream
except (ImportError, AttributeError):
    pkg_resources = None


class Request(RequestBase):
    """Flask默认使用的请求对象，用来记住匹配的端点值（endpoint）和视图参数（view arguments）。

    这就是最终的request对象。如果你想替换掉这个请求对象，可以子类化这个
    类，然后将你的子类赋值给flask.Flask.request_class。

    The request object used by default in flask.  Remembers the
    matched endpoint and view arguments.
    
    It is what ends up as :class:`~flask.request`.  If you want to replace
    the request object used you can subclass this and set
    :attr:`~flask.Flask.request_class` to your subclass.
    """

    def __init__(self, environ):
        RequestBase.__init__(self, environ)
        self.endpoint = None
        self.view_args = None


class Response(ResponseBase):
    """Flask默认使用的响应对象。除了将MIME类型默认设置为HTML外，和Werkzeug提供的响应对象
    完全相同。通常情况下，你不需要自己创建这个对象，因为flask.Flask.make_response
    会负责这个工作。

    如果你想替换这个响应对象，你可以子类化这个类，然后将你的子类赋值给flask.Flask.request_class。

    The response object that is used by default in flask.  Works like the
    response object from Werkzeug but is set to have a HTML mimetype by
    default.  Quite often you don't have to create this object yourself because
    :meth:`~flask.Flask.make_response` will take care of that for you.

    If you want to replace the response object used you can subclass this and
    set :attr:`~flask.Flask.request_class` to your subclass.
    """
    default_mimetype = 'text/html'


class _RequestGlobals(object):
    pass


class _RequestContext(object):
    """请求上下文（request context）包含所有请求相关的信息。它会在请求进入时被创建，
    然后被推送到_request_ctx_stack，在请求结束时会被相应的移除。它会为提供的
    WSGI环境创建URL适配器（adapter）和请求对象。

    The request context contains all request relevant information.  It is
    created at the beginning of the request and pushed to the
    `_request_ctx_stack` and removed at the end of it.  It will create the
    URL adapter and request object for the WSGI environment provided.
    """

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
        # 在调试模式（debug mode）而且有异常发生时，不要移除（pop）请求堆栈。
        # 这将运行调试器（debugger）在交互式shell中仍然可以获取请求对象。
        # do not pop the request stack if we are in debug mode and an
        # exception happened.  This will allow the debugger to still
        # access the request object in the interactive shell.
        if tb is None or not self.app.debug:
            _request_ctx_stack.pop()


def url_for(endpoint, **values):
    """根据给定的端点和提供的方法生成一个URL。

    :param endpoint: URL的端点值（函数名）。
    :param values: URL规则的变量参数。
    
    Generates a URL to the given endpoint with the method provided.

    :param endpoint: the endpoint of the URL (name of the function)
    :param values: the variable arguments of the URL rule
    """
    return _request_ctx_stack.top.url_adapter.build(endpoint, values)


def flash(message):
    """闪现（flash）一个消息到下一个请求。为了从session中移除闪现过的消息
    并将其显示给用户，你必须在模板中调用get_flashed_messages。

    :param message: 被闪现的消息。

    Flashes a message to the next request.  In order to remove the
    flashed message from the session and to display it to the user,
    the template has to call :func:`get_flashed_messages`.

    :param message: the message to be flashed.
    """
    session['_flashes'] = (session.get('_flashes', [])) + [message]


def get_flashed_messages():
    """从session里拉取（pull）所有要闪现的消息并返回它们。在同一个请求对这个函数的
    进一步调用会返回同样的消息。

    Pulls all flashed messages from the session and returns them.
    Further calls in the same request to the function will return
    the same messages.
    """
    flashes = _request_ctx_stack.top.flashes
    if flashes is None:
        _request_ctx_stack.top.flashes = flashes = \
            session.pop('_flashes', [])
    return flashes


def render_template(template_name, **context):
    """使用给定的上下文从模板（template）文件夹渲染一个模板。
    
    :param template_name: 要被渲染的模板文件名。
    :param context: 在模板上下文中应该可用（available）的变量。

    Renders a template from the template folder with the given
    context.

    :param template_name: the name of the template to be rendered
    :param context: the variables that should be available in the
                    context of the template.
    """
    current_app.update_template_context(context)
    return current_app.jinja_env.get_template(template_name).render(context)


def render_template_string(source, **context):
    """使用给定的模板源代码字符串（source string）和上下文渲染一个模板。

    :param template_name: 要被渲染的模板源代码。
    :param context: 在模板上下文中应该可用的变量。

    Renders a template from the given template source string
    with the given context.

    :param template_name: the sourcecode of the template to be
                          rendered
    :param context: the variables that should be available in the
                    context of the template.
    """
    current_app.update_template_context(context)
    return current_app.jinja_env.from_string(source).render(context)


def _default_template_ctx_processor():
    """默认的模板上下文处理器（processor）。注入request、session和g。

    Default template context processor.  Injects `request`,
    `session` and `g`.
    """
    reqctx = _request_ctx_stack.top
    return dict(
        request=reqctx.request,
        session=reqctx.session,
        g=reqctx.g
    )


def _get_package_path(name):
    """返回包的路径，如果找不到则返回当前工作目录（cwd）
    Returns the path to a package or cwd if that cannot be found."""
    try:
        return os.path.abspath(os.path.dirname(sys.modules[name].__file__))
    except (KeyError, AttributeError):
        return os.getcwd()


class Flask(object):
    """这个flask对象实现了WSGI程序并作为中心对象存在。传入的参数为程序所在的模块
    或包的名称。一旦这个对象被创建，它将作为一个中心注册处，所有的视图函数、URL规则、
    模板配置等等都将注册到这里。

    包的名称被用来从包的内部或模块所在的文件夹解析资源，具体的位置取决于传入的包名称
    参数（package_name）指向一个真实的Python包（包含__init__.py文件的文件夹）
    还是一个标准的模块（.py文件）。

    关于资源加载的更多信息，参见open_resource。

    通常，你会在你的主脚本或包中的__init__.py文件里使用下面的方式创建一个Flask实例：

        from flask import Flask
        app = Flask(__name__)
    
    The flask object implements a WSGI application and acts as the central
    object.  It is passed the name of the module or package of the
    application.  Once it is created it will act as a central registry for
    the view functions, the URL rules, template configuration and much more.

    The name of the package is used to resolve resources from inside the
    package or the folder the module is contained in depending on if the
    package parameter resolves to an actual python package (a folder with
    an `__init__.py` file inside) or a standard module (just a `.py` file).

    For more information about resource loading, see :func:`open_resource`.

    Usually you create a :class:`Flask` instance in your main module or
    in the `__init__.py` file of your package like this::

        from flask import Flask
        app = Flask(__name__)
    """

    #: 用作请求对象的类。更多信息参见flask.request。
    #: the class that is used for request objects.  See :class:`~flask.request`
    #: for more information.
    request_class = Request

    #: 用作响应对象的类。更多信息参见flask.Response。
    #: the class that is used for response objects.  See
    #: :class:`~flask.Response` for more information.
    response_class = Response

    #: 静态文件的路径。如果你不想使用静态文件，可以将这个值设为None，这样不会添加
    #: 相应的URL规则而且开发服务器将不再提供（serve）任何静态文件。
    #: path for the static files.  If you don't want to use static files
    #: you can set this value to `None` in which case no URL rule is added
    #: and the development server will no longer serve any static files.
    static_path = '/static'

    #: 如果设置了密钥（secret key），加密组件可以使用它来为cookies或其他东西签名。
    #: 比如，当你想使用安全的cookie时，把它设为一个复杂的随机值。
    #: if a secret key is set, cryptographic components can use this to
    #: sign cookies and other things.  Set this to a complex random value
    #: when you want to use the secure cookie for instance.
    secret_key = None

    #: 安全cookie使用这个值作为session cookie的名称。
    #: The secure cookie uses this for the name of the session cookie
    session_cookie_name = 'session'

    #: 直接传入Jinja2环境的选项。
    #: options that are passed directly to the Jinja2 environment
    jinja_options = dict(
        autoescape=True,
        extensions=['jinja2.ext.autoescape', 'jinja2.ext.with_']
    )

    def __init__(self, package_name):
        #: 调试标志。将它设为True来开启调试模式。在调试模式下，当一个未捕捉
        #: 的异常触发时，调试器会启动；而且，当代码中的变动被探测到时，开发
        #: 服务器会自动重载程序。
        #: the debug flag.  Set this to `True` to enable debugging of
        #: the application.  In debug mode the debugger will kick in
        #: when an unhandled exception ocurrs and the integrated server
        #: will automatically reload the application if changes in the
        #: code are detected.
        self.debug = False

        #: 包或模块的名称。一旦它通过构造器设置后，就不要更改这个值。
        #: the name of the package or module.  Do not change this once
        #: it was set by the constructor.
        self.package_name = package_name

        #: 哪里是定位到的程序根目录？
        #: where is the app root located?
        self.root_path = _get_package_path(self.package_name)

        #: 储存所有已注册的视图函数的字典。字典的键将是函数的名称，这些名称
        #: 也被用来生成URL；字典的值是函数对象本身。
        #: 要注册一个视图函数，使用route装饰器（decorator）。
        #: a dictionary of all view functions registered.  The keys will
        #: be function names which are also used to generate URLs and
        #: the values are the function objects themselves.
        #: to register a view function, use the :meth:`route` decorator.
        self.view_functions = {}

        #: 储存所有已注册的错误处理器的字典。字段的键是整型（integer）类型的
        #: 错误码，字典的值是处理对应错误的函数。
        #: 要注册一个错误处理器，使用errorhandler装饰器。
        #: a dictionary of all registered error handlers.  The key is
        #: be the error code as integer, the value the function that
        #: should handle that error.
        #: To register a error handler, use the :meth:`errorhandler`
        #: decorator.
        self.error_handlers = {}

        #: 应该在请求开始进入时、请求分发开始前调用的函数列表。举例来说，
        #: 这可以用来打开数据库连接或获取当前登录的用户。
        #: 要注册一个函数到这里，使用before_request装饰器。
        #: a list of functions that should be called at the beginning
        #: of the request before request dispatching kicks in.  This
        #: can for example be used to open database connections or
        #: getting hold of the currently logged in user.
        #: To register a function here, use the :meth:`before_request`
        #: decorator.
        self.before_request_funcs = []

        #: 应该在请求处理结束时调用的函数列表。这些函数会被传入当前的响应
        #: 对象，你可以在函数内修改或替换它。
        #: 要注册一个函数到这里，使用after_request装饰器。
        #: a list of functions that are called at the end of the
        #: request.  Tha function is passed the current response
        #: object and modify it in place or replace it.
        #: To register a function here use the :meth:`after_request`
        #: decorator.
        self.after_request_funcs = []

        #: 将被无参数调用以生成模板上下文的的函数列表。每一个函数应返回一个
        #: 用于更新模板上下文的字典。
        #: 要注册一个函数到这里，使用context_processor装饰器。
        #: a list of functions that are called without arguments
        #: to populate the template context.  Each returns a dictionary
        #: that the template context is updated with.
        #: To register a function here, use the :meth:`context_processor`
        #: decorator.
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

        #: Jinja2环境。它通过jinja_options创建，加载器（loader）通过
        #: create_jinja_loader函数返回。
        #: the Jinja2 environment.  It is created from the
        #: :attr:`jinja_options` and the loader that is returned
        #: by the :meth:`create_jinja_loader` function.
        self.jinja_env = Environment(loader=self.create_jinja_loader(),
                                     **self.jinja_options)
        self.jinja_env.globals.update(
            url_for=url_for,
            get_flashed_messages=get_flashed_messages
        )

    def create_jinja_loader(self):
        """创建Jinja加载器。默认只是返回一个对应配置好的包的包加载器，它会从
        templates文件夹中寻找模板。要添加其他加载器，可以重载这个方法。
        Creates the Jinja loader.  By default just a package loader for
        the configured package is returned that looks up templates in the
        `templates` folder.  To add other loaders it's possible to
        override this method.
        """
        if pkg_resources is None:
            return FileSystemLoader(os.path.join(self.root_path, 'templates'))
        return PackageLoader(self.package_name)

    def update_template_context(self, context):
        """使用常用的变量更新模板上下文。这会注入request、session和g到模板上下文中。

        :param context: 包含额外添加的变量的字典，用来更新上下文。

        Update the template context with some commonly used variables.
        This injects request, session and g into the template context.

        :param context: the context as a dictionary that is updated in place
                        to add extra variables.
        """
        reqctx = _request_ctx_stack.top
        for func in self.template_context_processors:
            context.update(func())

    def run(self, host='localhost', port=5000, **options):
        """在本地开发服务器上运行程序。如果debug标志被设置，这个服务器
        会在代码更改时自动重载，并会在异常发生时显示一个调试器。
        
        :param host: 监听的主机名。设为'0.0.0.0'可以让服务器外部可见。
        :param port: 服务器的端口。
        :param options: 这些选项将被转发给底层的Werkzeug服务器。更多信息
                        参见werkzeug.run_simple。

        Runs the application on a local development server.  If the
        :attr:`debug` flag is set the server will automatically reload
        for code changes and show a debugger in case an exception happened.

        :param host: the hostname to listen on.  set this to ``'0.0.0.0'``
                     to have the server available externally as well.
        :param port: the port of the webserver
        :param options: the options to be forwarded to the underlying
                        Werkzeug server.  See :func:`werkzeug.run_simple`
                        for more information.
        """
        from werkzeug import run_simple
        if 'debug' in options:
            self.debug = options.pop('debug')
        options.setdefault('use_reloader', self.debug)
        options.setdefault('use_debugger', self.debug)
        return run_simple(host, port, self, **options)

    def test_client(self):
        """为这个程序创建一个测试客户端。
        Creates a test client for this application.  For information
        about unit testing head over to :ref:`testing`.
        """
        from werkzeug import Client
        return Client(self, self.response_class, use_cookies=True)

    def open_resource(self, resource):
        """从程序的资源文件夹打开一个资源。至于它是如何工作的，考虑下面的文件
        目录：

            /myapplication.py
            /schemal.sql
            /static
                /style.css
            /template
                /layout.html
                /index.html

        如果你想打开schema.sql文件，可以这样做：

            with app.open_resource('schema.sql') as f:
                contents = f.read()
                do_something_with(contents)

        :param resource: 资源文件的名称。要获取子文件夹中的资源，使用斜线作为分界符。
        
        Opens a resource from the application's resource folder.  To see
        how this works, consider the following folder structure::

            /myapplication.py
            /schemal.sql
            /static
                /style.css
            /template
                /layout.html
                /index.html

        If you want to open the `schema.sql` file you would do the
        following::

            with app.open_resource('schema.sql') as f:
                contents = f.read()
                do_something_with(contents)

        :param resource: the name of the resource.  To access resources within
                         subfolders use forward slashes as separator.
        """
        if pkg_resources is None:
            return open(os.path.join(self.root_path, resource), 'rb')
        return pkg_resources.resource_stream(self.package_name, resource)

    def open_session(self, request):
        """创建或打开一个新的session。默认的实现是存储所有的用户会话（session）
        数据到一个签名的cookie中。这需要secret_key属性被设置。

        :param request: request_class的实例。

        Creates or opens a new session.  Default implementation stores all
        session data in a signed cookie.  This requires that the
        :attr:`secret_key` is set.

        :param request: an instance of :attr:`request_class`.
        """
        key = self.secret_key
        if key is not None:
            return SecureCookie.load_cookie(request, self.session_cookie_name,
                                            secret_key=key)

    def save_session(self, session, response):
        """如果需要更新，保存session。默认实现参见open_session。
        
        :param session: 要被保存的session
                        （一个werkzeug.contrib.securecookie.SecureCookie对象）
        :param response: 一个response_class实例。

        Saves the session if it needs updates.  For the default
        implementation, check :meth:`open_session`.

        :param session: the session to be saved (a
                        :class:`~werkzeug.contrib.securecookie.SecureCookie`
                        object)
        :param response: an instance of :attr:`response_class`
        """
        if session is not None:
            session.save_cookie(response, self.session_cookie_name)

    def add_url_rule(self, rule, endpoint, **options):
        """连接一个URL规则。效果和route装饰器完全相同，不过不会为端点注册视图函数。

        基本示例：

            @app.route('/')
            def index():
                pass

        和下面相同：

            def index():
                pass
            app.add_url_rule('index', '/')
            app.view_functions['index'] = index

        :param rule: 字符串形式的URL规则。
        :param endpoint: 对应被注册的URL规则的端点。Flask默认将视图函数名作为端点。
        :param options: 转发给底层的werkzeug.routing.Rule对象的选项。
        
        Connects a URL rule.  Works exactly like the :meth:`route`
        decorator but does not register the view function for the endpoint.

        Basically this example::

            @app.route('/')
            def index():
                pass

        Is equivalent to the following::

            def index():
                pass
            app.add_url_rule('index', '/')
            app.view_functions['index'] = index

        :param rule: the URL rule as string
        :param endpoint: the endpoint for the registered URL rule.  Flask
                         itself assumes the name of the view function as
                         endpoint
        :param options: the options to be forwarded to the underlying
                        :class:`~werkzeug.routing.Rule` object
        """
        options['endpoint'] = endpoint
        options.setdefault('methods', ('GET',))
        self.url_map.add(Rule(rule, **options))

    def route(self, rule, **options):
        """一个用于为给定的URL规则注册视图函数的装饰器。示例：

            @app.route('/')
            def index():
                return 'Hello World'

        路由中的变量部分可以使用尖括号来指定（/user/<username>）。默认情况下，
        URL中的变量部分接受任意不包含斜线的字符串，你也可以使用<converter:name>
        的形式来指定一个不同的转换器。

        变量部分将被作为关键字参数传入视图函数。

        可用的转换器如下所示：

        ========= =======================================
        int       接受整型
        float     类似int，但是接受浮点数（floating point）
        path      类似默认值，但接受斜线
        ========= =======================================

        下面是一些示例：

            @app.route('/')
            def index():
                pass

            @app.route('/<username>')
            def show_user(username):
                pass

            @app.route('/post/<int:post_id>')
            def show_post(post_id):
                pass

        一个重要的细节是留意Flask是如何处理斜线的。为了让每一个URL独一无二，
        下面的规则被应用：

        1. 如果一个规则以一个斜线结尾而用户请求的地址不包含斜线，那么该用户
        会被重定向到相同的页面并附加一个结尾斜线。
        2. 如果一个规则没有以斜线结尾而用户请求的页面包含了一个结尾斜线，
        会抛出一个404错误。
        
        这和Web服务器处理静态文件的方式相一致。这也可以让你安全的使用相对链接目标。

        这个route装饰器也接受一系列参数：

        :param rule: 字符串形式的URL规则
        :param methods: 一个方法列表，可用的值限定为（GET、POST等）。默认一个
                        规则仅监听GET（以及隐式的HEAD）
        :param subdomain: 当子域名匹配使用时，为规则指定子域。
        :param strict_slashes: 可以用来为这个规则关闭严格的斜线设置，见上。
        :param options: 转发到底层的werkzeug.routing.Rule对象的其他选项。
        
        
        A decorator that is used to register a view function for a
        given URL rule.  Example::

            @app.route('/')
            def index():
                return 'Hello World'

        Variables parts in the route can be specified with angular
        brackets (``/user/<username>``).  By default a variable part
        in the URL accepts any string without a slash however a different
        converter can be specified as well by using ``<converter:name>``.

        Variable parts are passed to the view function as keyword
        arguments.

        The following converters are possible:

        =========== ===========================================
        `int`       accepts integers
        `float`     like `int` but for floating point values
        `path`      like the default but also accepts slashes
        =========== ===========================================

        Here some examples::

            @app.route('/')
            def index():
                pass

            @app.route('/<username>')
            def show_user(username):
                pass

            @app.route('/post/<int:post_id>')
            def show_post(post_id):
                pass

        An important detail to keep in mind is how Flask deals with trailing
        slashes.  The idea is to keep each URL unique so the following rules
        apply:

        1. If a rule ends with a slash and is requested without a slash
           by the user, the user is automatically redirected to the same
           page with a trailing slash attached.
        2. If a rule does not end with a trailing slash and the user request
           the page with a trailing slash, a 404 not found is raised.

        This is consistent with how web servers deal with static files.  This
        also makes it possible to use relative link targets safely.

        The :meth:`route` decorator accepts a couple of other arguments
        as well:

        :param rule: the URL rule as string
        :param methods: a list of methods this rule should be limited
                        to (``GET``, ``POST`` etc.).  By default a rule
                        just listens for ``GET`` (and implicitly ``HEAD``).
        :param subdomain: specifies the rule for the subdoain in case
                          subdomain matching is in use.
        :param strict_slashes: can be used to disable the strict slashes
                               setting for this rule.  See above.
        :param options: other options to be forwarded to the underlying
                        :class:`~werkzeug.routing.Rule` object.
        """
        def decorator(f):
            self.add_url_rule(rule, f.__name__, **options)
            self.view_functions[f.__name__] = f
            return f
        return decorator

    def errorhandler(self, code):
        """一个用于为给定的错误码注册函数的装饰器。示例：

            @app.errorhandler(404)
            def page_not_found():
                return 'This page does not exist', 404

        你也可以不使用errorhandler注册一个函数作为错误处理器。下面的例子同上：

            def page_not_found():
                return 'This page does not exist', 404
            app.error_handlers[404] = page_not_found

        :param code: 对应处理器的整型类型的错误代码。
        
        A decorator that is used to register a function give a given
        error code.  Example::

            @app.errorhandler(404)
            def page_not_found():
                return 'This page does not exist', 404

        You can also register a function as error handler without using
        the :meth:`errorhandler` decorator.  The following example is
        equivalent to the one above::

            def page_not_found():
                return 'This page does not exist', 404
            app.error_handlers[404] = page_not_found

        :param code: the code as integer for the handler
        """
        def decorator(f):
            self.error_handlers[code] = f
            return f
        return decorator

    def before_request(self, f):
        """注册一个函数，则每一个请求处理前调用。
        
        Registers a function to run before each request."""
        self.before_request_funcs.append(f)
        return f

    def after_request(self, f):
        """注册一个函数，在每一个请求处理后调用。

        Register a function to be run after each request."""
        self.after_request_funcs.append(f)
        return f

    def context_processor(self, f):
        """注册一个模板上下文处理函数。

        Registers a template context processor function."""
        self.template_context_processors.append(f)
        return f

    def match_request(self):
        """基于URL映射（map）匹配当前请求。如果匹配成功，同时也存储端点和
        视图参数，否则存储异常。

        Matches the current request against the URL map and also
        stores the endpoint and view arguments on the request object
        is successful, otherwise the exception is stored.
        """
        rv = _request_ctx_stack.top.url_adapter.match()
        request.endpoint, request.view_args = rv
        return rv

    def dispatch_request(self):
        """附注请求分发工作。匹配URL，返回视图函数或错误器的返回值。这个返回值
        不一定得是响应对象。为了将返回值返回值转换成合适的想要对象，
        调用make_response。
        
        Does the request dispatching.  Matches the URL and returns the
        return value of the view or error handler.  This does not have to
        be a response object.  In order to convert the return value to a
        proper response object, call :func:`make_response`.
        """
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
        """将视图函数的返回值转换成一个真正的响应对象，即response_class实例。

        rv允许的类型如下所示：

        ======================= ===============================================
        response_class          这个对象将被直接返回
        str                     使用这个字符串作为主体创建一个请求对象
        unicode                 将这个字符串进行utf-8编码后作为主体创建一个请求对象
        tuple                   使用这个元组的内容作为参数创建一个请求对象
        a WSGI function         这个函数将作为WSGI程序调用并缓存为响应对象
        ======================= ===============================================

        :param rv: 视图函数返回值

        Converts the return value from a view function to a real
        response object that is an instance of :attr:`response_class`.

        The following types are allowd for `rv`:

        ======================= ===========================================
        :attr:`response_class`  the object is returned unchanged
        :class:`str`            a response object is created with the
                                string as body
        :class:`unicode`        a response object is created with the
                                string encoded to utf-8 as body
        :class:`tuple`          the response object is created with the
                                contents of the tuple as arguments
        a WSGI function         the function is called as WSGI application
                                and buffered as response object
        ======================= ===========================================

        :param rv: the return value from the view function
        """
        if isinstance(rv, self.response_class):
            return rv
        if isinstance(rv, basestring):
            return self.response_class(rv)
        if isinstance(rv, tuple):
            return self.response_class(*rv)
        return self.response_class.force_type(rv, request.environ)

    def preprocess_request(self):
        """在实际的请求分发之前调用，而且将会调用每一个使用before_request
        装饰的函数。如果其中某一个函数返回一个值，这个值将会作为视图返回值
        处理并停止进一步的请求处理。

        Called before the actual request dispatching and will
        call every as :meth:`before_request` decorated function.
        If any of these function returns a value it's handled as
        if it was the return value from the view and further
        request handling is stopped.
        """
        for func in self.before_request_funcs:
            rv = func()
            if rv is not None:
                return rv

    def process_response(self, response):
        """为了在发送给WSGI服务器前修改响应对象，可以重写这个方法。 默认
        这会调用所有使用after_request装饰的函数。

        :param response: 一个response_class对象。
        :return: 一个新的响应对象或原对象，必须是response_class实例。

        Can be overridden in order to modify the response object
        before it's sent to the WSGI server.  By default this will
        call all the :meth:`after_request` decorated functions.

        :param response: a :attr:`response_class` object.
        :return: a new response object or the same, has to be an
                 instance of :attr:`response_class`.
        """
        session = _request_ctx_stack.top.session
        if session is not None:
            self.save_session(session, response)
        for handler in self.after_request_funcs:
            response = handler(response)
        return response

    def wsgi_app(self, environ, start_response):
        """实际的WSGI程序。它没有通过__call__实现，因此可以附加中间件：
        
            app.wsgi_app = MyMiddleware(app.wsgi_app)

        :param environ: 一个WSGI环境。
        :param start_response: 一个接受状态码的可调用对象，一个包含首部
                               的列表以及一个可选的用于启动响应的异常上下文

        The actual WSGI application.  This is not implemented in
        `__call__` so that middlewares can be applied:

            app.wsgi_app = MyMiddleware(app.wsgi_app)

        :param environ: a WSGI environment
        :param start_response: a callable accepting a status code,
                               a list of headers and an optional
                               exception context to start the response
        """
        with self.request_context(environ):
            rv = self.preprocess_request()
            if rv is None:
                rv = self.dispatch_request()
            response = self.make_response(rv)
            response = self.process_response(response)
            return response(environ, start_response)

    def request_context(self, environ):
        """从给定的环境创建一个请求上下文，并将其绑定到当前上下文。这必须搭配with
        语句使用，因为请求仅绑定在with块中的当前上下文里。

        用法示例：
            
            with app.request_context(environ):
                do_something_with(request)

        :params environ: 一个WSGI环境。

        Creates a request context from the given environment and binds
        it to the current context.  This must be used in combination with
        the `with` statement because the request is only bound to the
        current context for the duration of the `with` block.

        Example usage::

            with app.request_context(environ):
                do_something_with(request)

        :params environ: a WSGI environment
        """
        return _RequestContext(self, environ)

    def test_request_context(self, *args, **kwargs):
        """从给定的值创建一个WSGI环境（更多信息请参见werkzeug.create_environ，
        这个函数接受相同的参数）。

        Creates a WSGI environment from the given values (see
        :func:`werkzeug.create_environ` for more information, this
        function accepts the same arguments).
        """
        return self.request_context(create_environ(*args, **kwargs))

    def __call__(self, environ, start_response):
        """wsgi_app的快捷方式。Shortcut for :attr:`wsgi_app`"""
        return self.wsgi_app(environ, start_response)


# 本地上下文
# context locals
_request_ctx_stack = LocalStack()
current_app = LocalProxy(lambda: _request_ctx_stack.top.app)
request = LocalProxy(lambda: _request_ctx_stack.top.request)
session = LocalProxy(lambda: _request_ctx_stack.top.session)
g = LocalProxy(lambda: _request_ctx_stack.top.g)
