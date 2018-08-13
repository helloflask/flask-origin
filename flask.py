# -*- coding: utf-8 -*-
"""
    Flask-Origin
    ~~~~~~~~~~~~~
     
    Flask 0.1版本源码注解。

    :copyright: (c) 2018 Grey Li
    :license: MIT, see LICENSE for more details.
"""
from __future__ import with_statement
import os
import sys

from jinja2 import Environment, PackageLoader, FileSystemLoader
from werkzeug import Request as RequestBase, Response as ResponseBase, \
     LocalStack, LocalProxy, create_environ, SharedDataMiddleware
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import HTTPException
from werkzeug.contrib.securecookie import SecureCookie

# 这些从Werkzeug和Jinja2导入的辅助函数（utilities）没有在
# 模块内使用，而是直接作为外部接口开放
from werkzeug import abort, redirect
from jinja2 import Markup, escape

# 优先使用pkg_resource，如果无法工作则使用cwd。
try:
    import pkg_resources
    pkg_resources.resource_stream
except (ImportError, AttributeError):
    pkg_resources = None


class Request(RequestBase):
    """Flask默认使用的请求对象，用来记住匹配的端点值（endpoint）和视图参数（view arguments）。

    这就是最终的flask.request对象。如果你想替换掉这个请求对象，可以子类化这个
    类，然后将你的子类赋值给flask.Flask.request_class。
    """

    def __init__(self, environ):
        RequestBase.__init__(self, environ)
        self.endpoint = None  # 当前请求的端点
        self.view_args = None  # 当前请求的视图参数，会作为关键字参数传入视图函数


class Response(ResponseBase):
    """Flask默认使用的响应对象。除了将MIME类型默认设置为HTML外，和Werkzeug提供的响应对象
    完全相同。通常情况下，你不需要自己创建这个对象，因为flask.Flask.make_response
    会负责这个工作。

    如果你想替换这个响应对象，你可以子类化这个类，然后将你的子类赋值给flask.Flask.response_class。
    """
    default_mimetype = 'text/html'


class _RequestGlobals(object):
    pass


class _RequestContext(object):
    """请求上下文（request context）包含所有请求相关的信息。它会在请求进入时被创建，
    然后被推送到_request_ctx_stack，在请求结束时会被相应的移除。它会为提供的
    WSGI环境创建URL适配器（adapter）和请求对象。
    """
    # 会在flask.Flask.request_context和flask.Flask.test_requset_context方法中
    # 调用，以便生成请求上下文。
    def __init__(self, app, environ):
        self.app = app
        self.url_adapter = app.url_map.bind_to_environ(environ)  # 绑定了当前环境信息，用于构建URL，在url_for函数中使用
        self.request = app.request_class(environ)  # 创建请求对象，包含请求信息
        self.session = app.open_session(self.request)  # 创建session对象，用于存储用户会话数据到cookie中
        self.g = _RequestGlobals()  # 创建g对象，用于在当前请求存储全局变量
        self.flashes = None  # 存储当前请求的通过flash函数发送的消息

    def __enter__(self):
        _request_ctx_stack.push(self)  # 将当前请求上下文对象推送到_request_ctx_stack堆栈，这个堆栈在最后定义

    def __exit__(self, exc_type, exc_value, tb):
        # 在调试模式（debug mode）而且有异常发生时，不要移除（pop）请求堆栈。
        # 这将允许调试器（debugger）在交互式shell中仍然可以获取请求对象。
        if tb is None or not self.app.debug:
            _request_ctx_stack.pop()


def url_for(endpoint, **values):
    """根据给定的端点和提供的方法生成一个URL。

    对于目标端点未知的变量参数，将会作为查询参数附加在URL后面（生成查询字符串）。

    :param endpoint: URL的端点值（函数名）。
    :param values: URL规则的变量参数。
    """
    return _request_ctx_stack.top.url_adapter.build(endpoint, values)  # 这里堆栈的栈顶（top）即上面的请求上下文对象实例


def flash(message):
    """闪现（flash）一个消息到下一个请求。为了从session中移除闪现过的消息
    并将其显示给用户，你必须在模板中调用get_flashed_messages。

    :param message: 被闪现的消息。
    """
    session['_flashes'] = (session.get('_flashes', [])) + [message]


def get_flashed_messages():
    """从session里拉取（pull）所有要闪现的消息并返回它们。在同一个请求中对这个函数的
    进一步调用会返回同样的消息。
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
    """
    current_app.update_template_context(context)
    return current_app.jinja_env.get_template(template_name).render(context)


def render_template_string(source, **context):
    """使用给定的模板源代码字符串（source string）和上下文渲染一个模板。

    :param template_name: 要被渲染的模板源代码。
    :param context: 在模板上下文中应该可用的变量。
    """
    current_app.update_template_context(context)
    return current_app.jinja_env.from_string(source).render(context)


def _default_template_ctx_processor():
    """默认的模板上下文处理器（processor）。注入request、session和g。"""
    # 把request、session和g注入到模板上下文，以便可以直接在模板中使用这些变量。
    reqctx = _request_ctx_stack.top
    return dict(
        request=reqctx.request,
        session=reqctx.session,
        g=reqctx.g
    )


def _get_package_path(name):
    """返回包的路径，如果找不到则返回当前工作目录（cwd）。"""
    try:
        return os.path.abspath(os.path.dirname(sys.modules[name].__file__))
    except (KeyError, AttributeError):
        return os.getcwd()


class Flask(object):
    """这个flask对象实现了WSGI程序并作为中心对象存在。传入的参数（package_name）为
    程序所在的模块或包的名称。一旦这个对象被创建，它将作为一个中心注册处，所有的视图
    函数、URL规则、模板配置等等都将注册到这里。

    包的名称被用来从包的内部或模块所在的文件夹解析资源，具体的位置取决于传入的包名称
    参数（package_name）指向一个真实的Python包（包含__init__.py文件的文件夹）
    还是一个标准的模块（.py文件）。

    关于资源加载的更多信息，参见open_resource。

    通常，你会在你的主脚本或包中的__init__.py文件里使用下面的方式创建一个Flask实例：

        from flask import Flask
        app = Flask(__name__)
    
    """

    #: 用作请求对象的类。更多信息参见flask.Request。
    request_class = Request

    #: 用作响应对象的类。更多信息参见flask.Response。
    response_class = Response

    #: 静态文件的路径。如果你不想使用静态文件，可以将这个值设为None，这样不会添加
    #: 相应的URL规则，而且开发服务器将不再提供（serve）任何静态文件。
    static_path = '/static'

    #: 如果设置了密钥（secret key），加密组件可以使用它来为
    #: cookies或其他东西签名。比如，当你想使用安全的cookie时，把它设为一个复杂的随机值。
    secret_key = None

    #: 安全cookie使用这个值作为session cookie的名称。
    session_cookie_name = 'session'  # 存储session对象数据的cookie名称

    #: 直接传入Jinja2环境的选项。
    jinja_options = dict(
        autoescape=True,  # 默认开启自动转义，即转义不安全字符为HTML实体，比如“>”、“<”等。
        extensions=['jinja2.ext.autoescape', 'jinja2.ext.with_']
    )

    def __init__(self, package_name):
        #: 调试标志。将它设为True来开启调试模式。在调试模式下，当一个未捕捉
        #: 的异常触发时，调试器会启动；而且，当代码中的变动被探测到时，开发
        #: 服务器会自动重载程序。
        self.debug = False

        #: 包或模块的名称。一旦它通过构造器设置后，就不要更改这个值。
        self.package_name = package_name

        #: 定位程序的根目录。
        self.root_path = _get_package_path(self.package_name)

        ###################################
        # 下面是几个存储回调函数的字典或列表
        ###################################

        #: 一个储存所有已注册的视图函数的字典。字典的键将是函数的名称，这些名称
        #: 也被用来生成URL；字典的值是函数对象本身。
        #: 要注册一个视图函数，使用route装饰器（decorator）。
        self.view_functions = {}

        #: 一个储存所有已注册的错误处理器的字典。字段的键是整型（integer）类型的
        #: 错误码，字典的值是处理对应错误的函数。
        #: 要注册一个错误处理器，使用errorhandler装饰器。
        self.error_handlers = {}

        #: 一个应该在请求开始进入时、请求分发开始前调用的函数列表。举例来说，
        #: 这可以用来打开数据库连接或获取当前登录的用户。
        #: 要注册一个函数到这里，使用before_request装饰器。
        self.before_request_funcs = []

        #: 一个应该在请求处理结束时调用的函数列表。这些函数会被传入当前的响应
        #: 对象，你可以在函数内修改或替换它。
        #: 要注册一个函数到这里，使用after_request装饰器。
        self.after_request_funcs = []

        #: 一个将被无参数调用以生成模板上下文的的函数列表。每一个函数应返回一个
        #: 用于更新模板上下文的字典。
        #: 要注册一个函数到这里，使用context_processor装饰器。
        self.template_context_processors = [_default_template_ctx_processor]  # 默认的处理器用来注入session、request和g

        self.url_map = Map()

        if self.static_path is not None:
            self.url_map.add(Rule(self.static_path + '/<filename>',
                                  build_only=True, endpoint='static'))
            if pkg_resources is not None:
                target = (self.package_name, 'static')
            else:
                target = os.path.join(self.root_path, 'static')
            self.wsgi_app = SharedDataMiddleware(self.wsgi_app, {  # SharedDataMiddleware中间件用来为程序添加处理静态文件的能力
                self.static_path: target  # URL路径和实际文件目录（static文件夹）的映射
            })

        #: Jinja2环境。它通过jinja_options创建，加载器（loader）通过
        #: create_jinja_loader函数返回。
        self.jinja_env = Environment(loader=self.create_jinja_loader(),
                                     **self.jinja_options)
        self.jinja_env.globals.update(  # 将url_for和get_flashed_messages函数作为全局对象注入到模板上下文，以便在模板中调用
            url_for=url_for,
            get_flashed_messages=get_flashed_messages
        )

    def create_jinja_loader(self):
        """创建Jinja加载器。默认只是返回一个对应配置好的包的包加载器，它会从
        templates文件夹中寻找模板。要添加其他加载器，可以重载这个方法。
        """
        if pkg_resources is None:
            return FileSystemLoader(os.path.join(self.root_path, 'templates'))
        return PackageLoader(self.package_name)

    def update_template_context(self, context):
        """使用常用的变量更新模板上下文。这会注入request、session和g到模板上下文中。

        :param context: 包含额外添加的变量的字典，用来更新上下文。
        """
        reqctx = _request_ctx_stack.top
        for func in self.template_context_processors:  # 调用所有使用context_processor装饰器注册的模板上下文处理函数，更新模板上下文
            context.update(func())

    def run(self, host='localhost', port=5000, **options):
        """在本地开发服务器上运行程序。如果debug标志被设置，这个服务器
        会在代码更改时自动重载，并会在异常发生时显示一个调试器。
        
        :param host: 监听的主机名。设为'0.0.0.0'可以让服务器外部可见。
        :param port: 服务器的端口。
        :param options: 这些选项将被转发给底层的Werkzeug服务器。更多信息
                        参见werkzeug.run_simple。
        """
        from werkzeug import run_simple
        if 'debug' in options:
            self.debug = options.pop('debug')
        options.setdefault('use_reloader', self.debug)  # 如果debug为True，开启重载器（reloader）
        options.setdefault('use_debugger', self.debug)  # 如果debug为True，开启调试器（debugger）
        return run_simple(host, port, self, **options)

    def test_client(self):
        """为这个程序创建一个测试客户端。"""
        from werkzeug import Client
        return Client(self, self.response_class, use_cookies=True)

    def open_resource(self, resource):
        """从程序的资源文件夹打开一个资源。至于它是如何工作的，考虑下面的文件
        目录：

            /myapplication.py
            /schemal.sql
            /static
                /style.css
            /templates
                /layout.html
                /index.html

        如果你想打开schema.sql文件，可以这样做：

            with app.open_resource('schema.sql') as f:
                contents = f.read()
                do_something_with(contents)

        :param resource: 资源文件的名称。要获取子文件夹中的资源，使用斜线作为分界符。
        """
        if pkg_resources is None:
            return open(os.path.join(self.root_path, resource), 'rb')
        return pkg_resources.resource_stream(self.package_name, resource)

    def open_session(self, request):
        """创建或打开一个新的session。默认的实现是存储所有的用户会话（session）
        数据到一个签名的cookie中。这需要secret_key属性被设置。

        :param request: request_class的实例。
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
            app.add_url_rule('/', 'index')
            app.view_functions['index'] = index

        :param rule: 字符串形式的URL规则。
        :param endpoint: 对应被注册的URL规则的端点。Flask默认将视图函数名作为端点。
        :param options: 转发给底层的werkzeug.routing.Rule对象的选项。
        """
        options['endpoint'] = endpoint
        options.setdefault('methods', ('GET',))  #  默认监听GET方法
        self.url_map.add(Rule(rule, **options))

    ################################################################################
    # 下面是几个用于注册各类回调函数的装饰器，函数对象存储到上面创建的几个字典和列表属性中  
    ################################################################################

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
        """
        def decorator(f):
            self.add_url_rule(rule, f.__name__, **options)
            self.view_functions[f.__name__] = f  # 将端点（默认使用函数名，即f.__name__）和函数对象的映射存储到view_functions字典
            return f
        return decorator

    def errorhandler(self, code):
        """一个用于为给定的错误码注册函数的装饰器。示例：

            @app.errorhandler(404)
            def page_not_found(error):
                return 'This page does not exist', 404

        你也可以不使用errorhandler注册一个函数作为错误处理器。下面的例子同上：

            def page_not_found(error):
                return 'This page does not exist', 404
            app.error_handlers[404] = page_not_found

        :param code: 对应处理器的整型类型的错误代码。
        """
        def decorator(f):
            self.error_handlers[code] = f  # 将错误码和函数对象的映射存储到error_handlers字典
            return f
        return decorator

    def before_request(self, f):
        """注册一个函数，则每一个请求处理前调用。"""
        self.before_request_funcs.append(f)
        return f

    def after_request(self, f):
        """注册一个函数，在每一个请求处理后调用。"""
        self.after_request_funcs.append(f)
        return f

    def context_processor(self, f):
        """注册一个模板上下文处理函数。"""
        self.template_context_processors.append(f)
        return f
    
    #################################
    # 下面的几个方法用于处理请求和响应
    #################################

    def match_request(self):
        """基于URL映射（map）匹配当前请求。如果匹配成功，同时也存储端点和
        视图参数，否则存储异常。
        """
        rv = _request_ctx_stack.top.url_adapter.match()
        request.endpoint, request.view_args = rv
        return rv

    def dispatch_request(self):
        """附注请求分发工作。匹配URL，返回视图函数或错误处理器的返回值。这个返回值
        不一定得是响应对象。为了将返回值返回值转换成合适的想要对象，调用make_response。
        """
        try:
            endpoint, values = self.match_request()
            return self.view_functions[endpoint](**values)  # 根据端点在view_functions字典内获取对应的视图函数并调用，传入视图参数
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
        """
        session = _request_ctx_stack.top.session
        if session is not None:
            self.save_session(session, response)
        for handler in self.after_request_funcs:
            response = handler(response)
        return response

    #########################################################################
    # WSGI规定的可调用对象，从请求进入，到生成响应并返回的整个处理流程都发生在这里
    #########################################################################

    def wsgi_app(self, environ, start_response):
        """实际的WSGI程序。它没有通过__call__实现，因此可以附加中间件：
        
            app.wsgi_app = MyMiddleware(app.wsgi_app)

        :param environ: 一个WSGI环境。
        :param start_response: 一个接受状态码的可调用对象，一个包含首部
                               的列表以及一个可选的用于启动响应的异常上下文。
        """
        # 在with语句下执行相关操作，会触发_RequestContext中的__enter__方法，从而推送请求上下文到堆栈中
        with self.request_context(environ):
            rv = self.preprocess_request()  # 预处理请求，调用所有使用了before_request钩子的函数
            if rv is None:
                rv = self.dispatch_request()  # 请求分发，获得视图函数返回值（或是错误处理器的返回值）
            response = self.make_response(rv)  # 生成响应，把上面的返回值转换成响应对象
            response = self.process_response(response)  # 响应处理，调用所有使用了after_request钩子的函数
            return response(environ, start_response)

    def request_context(self, environ):
        """从给定的环境创建一个请求上下文，并将其绑定到当前上下文。这必须搭配with
        语句使用，因为请求仅绑定在with块中的当前上下文里。

        用法示例：
            
            with app.request_context(environ):
                do_something_with(request)

        :param environ: 一个WSGI环境。
        """
        return _RequestContext(self, environ)

    def test_request_context(self, *args, **kwargs):
        """从给定的值创建一个WSGI环境（更多信息请参见werkzeug.create_environ，
        这个函数接受相同的参数）。
        """
        return self.request_context(create_environ(*args, **kwargs))

    def __call__(self, environ, start_response):
        """wsgi_app的快捷方式。"""
        return self.wsgi_app(environ, start_response)


# 本地上下文

# 请求上下文堆栈（_request_ctx_stack）栈顶（_request_ctx_stack.top）的对象即请求上下文对象（_RequestContext）实例
# 通过这里的调用可以获取当前请求上下文中保存的request、session等对象
# 请求上下文在wsgi_app方法中通过with语句调用request_context方法创建并推入堆栈

# 本地上下文相关的本地线程、本地堆栈和本地代理的实现这里不再展开，你需要先了解堆栈和代码在Python中的实现，
# 然后再通过阅读Werkzeug的文档或源码了解具体实现
# 另外，你也可以阅读《Flask Web开发实战》（helloflask.com/book）第16章16.4.3小节，这一小节首先介绍了本地线程和Werkzeug中实现的Local，
# 然后从堆栈和代理在Python中的基本实现开始，逐渐过渡到本地堆栈和本地代理的实现
_request_ctx_stack = LocalStack()
current_app = LocalProxy(lambda: _request_ctx_stack.top.app)
request = LocalProxy(lambda: _request_ctx_stack.top.request)
session = LocalProxy(lambda: _request_ctx_stack.top.session)
g = LocalProxy(lambda: _request_ctx_stack.top.g)
