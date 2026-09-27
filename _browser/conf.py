# -*- coding: utf-8 -*-
"""Sphinx configuration used by _browser/build.py (JSON builder only).

It mirrors the main conf.py but drops the Odoo HTML theme and replaces the
``demo:`` directives, which query a live Odoo demo server, with no-op stubs.
"""
import os
import sys

from docutils.parsers.rst import Directive, directives
from sphinx.domains import Domain

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC, '_extensions'))

extensions = [
    'sphinx.ext.ifconfig',
    'sphinx.ext.todo',
    'html_domain',
    'embedded_video',
]

master_doc = 'index'
project = u'Odoo'
release = version = '12.0'
exclude_patterns = ['locale', 'README.*', '_build', '_browser', 'bin', 'include', 'lib', '.*']
todo_include_todos = False
suppress_warnings = ['image.not_readable', 'toc.not_included', 'ref.ref']


class DemoStub(Directive):
    required_arguments = 1
    final_argument_whitespace = True
    has_content = True
    option_spec = {'view': directives.unchanged, 'only': directives.unchanged}

    def run(self):
        return []


class DemoDomain(Domain):
    name = 'demo'
    label = 'Odoo Demo'
    directives = {'fields': DemoStub, 'action': DemoStub}


def setup(app):
    app.add_domain(DemoDomain)
