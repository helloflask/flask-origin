# Flask-Origin

Flask [0.1](https://github.com/pallets/flask/tree/0.1)版本源码注解*。

*注解：这里的“注解” = 源码本身包含的注释、文档字符串的翻译与简化 + 添加更多必要的注释 + 添加更多有助于理解的额外提示*

## 源码版本

为了方便不同的阅读需求，源码设置了下面几个标签：

* mini：去除所有注释和文档字符串
* origin：原版
* translated：翻译所有注释和文档字符串
* annotated：添加注解

你可以使用下面的方式签出某个版本，以mini为例：

```
$ git clone https://github.com/greyli/flask-origin
$ cd flask-origin
$ git checkout mini
```

**注意：0.1版本源码中的部分API在最新版本已经发生了变化，请勿将源码中的API用于实际开发。**

## 阅读前

为了更容易理解Flask的实现原理，你需要对WSGI协议以及HTTP协议有一些了解，建议先简单浏览下面的基本知识：

* [PEP 0333](https://www.python.org/dev/peps/pep-0333/)和
[PEP 3333](https://www.python.org/dev/peps/pep-3333/)（WSGI实现）
* [HTTP概述](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Overview)

## 阅读后

Flask内部实现大量依赖于Werkzeug，包括请求和响应对象，路由匹配，URL生成等等，你可以阅读Werkzeug的文档来深入了解这些内容的具体实现。另外，如果你对模板渲染部分的内容感兴趣，也可以考虑阅读Jinja2文档：

* [Werkzeug文档](http://werkzeug.pocoo.org/docs/)
* [Jinja2文档](http://jinja.pocoo.org/docs/)

*注意：新版本的Werkzeug和Jinja2已经发生很大的变化，0.1版本的Flask对应的Werkzeug源码版本为[0.6.1](https://github.com/pallets/werkzeug/tree/0.6.1)，对应的Jinja2源码版本为[2.4](https://github.com/pallets/jinja/tree/2.4)。上述文档链接分别为0.14和2.9版本，请谨慎参考。*

## 下一步

由于篇幅所限，部分概念（比如本地上下文相关的本地线程、本地堆栈、本地代理）并没有深入介绍。而且，相对于通过代码编写的顺序从上往下阅读，通过调用逻辑阅读，或是以某个功能实现作为切入点来阅读可以更容易理解Flask的工作方式。为此，你可以考虑阅读[《Flask Web开发实战》](http://helloflask.com/book)第16章，它主要包含下面这些内容：

* WSGI实现介绍
* Flask设计理念
* Flask发行版本分析
* Flask工作原理和机制解析
  * Flask中的请求响应循环
  * 路由系统
  * 本地上下文
  * 请求与响应对象
  * session
  * 蓝本
  * 模板渲染

## License

本项目使用MIT协议授权，基于Flask原项目的BSD协议对相关文件进行了删改，具体参见`LICENSE`文件。
