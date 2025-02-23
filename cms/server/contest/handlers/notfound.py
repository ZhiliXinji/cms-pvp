try:
    import tornado4.web as tornado_web
except ImportError:
    import tornado.web as tornado_web

from .contest import ContestHandler


class NotFoundHandler(ContestHandler):
    def prepare(self):
        super().prepare()
        raise tornado_web.HTTPError(404)
