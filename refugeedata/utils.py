import re
from itertools import chain
import urllib

from django.utils.safestring import SafeData

import pyratemp
import six


class PyratempRegexFixMeta(type):

    """Pyratemp doesn't build its regexes properly. Fix that."""

    def __new__(cls, name, bases, dic):
        klass = type(name, bases, dic)
        # build regexps
        # comment
        #   single-line, until end-tag or end-of-line.
        klass._strComment = r"%s(?P<content>.*?)(?P<end>%s|\n|$)" % (
            re.escape(klass._comment_start), re.escape(klass._comment_end))
        klass._reComment = re.compile(klass._strComment, re.M)

        # escaped or unescaped substitution
        #   single-line ("|$" is needed to be able to generate good error-messges)
        klass._strSubstitution = r"""
            (
            %s\s*(?P<sub>.*?)\s*(?P<end>%s|$)       #substitution
            |
            %s\s*(?P<escsub>.*?)\s*(?P<escend>%s|$) #escaped substitution
            )
        """ % (re.escape(klass._sub_start), re.escape(klass._sub_end),
               re.escape(klass._subesc_start), re.escape(klass._subesc_end))
        klass._reSubstitution = re.compile(klass._strSubstitution, re.X | re.M)

        # block
        #   - single-line, no nesting.
        #   or
        #   - multi-line, nested by whitespace indentation:
        #       * start- and end-tag of a block must have exactly the same indentation.
        #       * start- and end-tags of *nested* blocks should have a greater indentation.
        # NOTE: A single-line block must not start at beginning of the line with
        #       the same indentation as the enclosing multi-line blocks!
        #       Note that "       " and "\t" are different, although they may
        #       look the same in an editor!
        _s = klass._block_start
        _e = klass._block_end
        klass._strBlock = r"""
                    ^(?P<mEnd>[ \t]*)%send%s(?P<meIgnored>.*)\r?\n?   # multi-line end  (^   <!--(end)-->IGNORED_TEXT\n)
                    |
                    (?P<sEnd>)%send%s                               # single-line end (<!--(end)-->)
                    |
                    (?P<sSpace>[ \t]*)                              # single-line tag (no nesting)
                    %s(?P<sKeyw>\w+)[ \t]*(?P<sParam>.*?)%s
                    (?P<sContent>.*?)
                    (?=(?:%s.*?%s.*?)??%send%s)                     # (match until end or i.e. <!--(elif/else...)-->)
                    |
                                                                    # multi-line tag, nested by whitespace indentation
                    ^(?P<indent>[ \t]*)                             #   save indentation of start tag
                    %s(?P<mKeyw>\w+)\s*(?P<mParam>.*?)%s(?P<mIgnored>.*)\r?\n
                    (?P<mContent>(?:.*\n)*?)
                    (?=(?P=indent)%s(?:.|\s)*?%s)                   #   match indentation
                """ % (_s, _e,
                       _s, _e,
                       _s, _e, _s, _e, _s, _e,
                       _s, _e, _s, _e)
        klass._reBlock = re.compile(klass._strBlock, re.X | re.M)
        return klass


@six.add_metaclass(PyratempRegexFixMeta)
class DjangoFormatParser(pyratemp.Parser):

    # template-syntax
    _comment_start = "{#"
    _comment_end = "#}"
    _subesc_start = "{{"
    _subesc_end = "}}"
    _block_start = "{%\s?"
    _block_end = "\s?%}"

    end_synonyms = frozenset(["endif", "endfor"])

    def __init__(self, filename=None, *args, **kwargs):
        self.filename = filename
        super(DjangoFormatParser, self).__init__(*args, **kwargs)

    def _parse(self, template, fpos=0):
        if self._includestack[-1][0] == None:
            self._includestack[-1] = (self.filename, self._includestack[-1][1])
        for end_synonym in self.end_synonyms:
            template = template.replace(end_synonym, "end")
        return super(DjangoFormatParser, self)._parse(template, fpos=fpos)


def test_expression(expr):
    if "|" in expr:
        raise SyntaxError("Django-style filters are not supported")


class PyratempTemplate(pyratemp.Renderer):

    """Wrapper around pyratemp.Template to use the Django template API"""

    def __init__(self, template_string, filename=None):
        superclass = DjangoFormatParser(filename, testexpr=test_expression)
        self.parsetree = superclass.parse(template_string)
        self.escapefunc = pyratemp.escape

    def evalfunc(self, expr, variables):
        return variables[expr]

    def render(self, context):
        assert isinstance(context, dict), context
        tokens = super(PyratempTemplate, self).render(self.parsetree, context)
        return pyratemp._dontescape("".join(tokens))


class TemplateWithDefaultFallback(PyratempTemplate):
    """Don't leave unresolved context variables empty; leave them as-is."""

    replace_variable_with = "{}"

    def evalfunc(self, expr, variables):
        value = variables.get(expr, "{{ %s }}" % expr)
        safe = isinstance(
            self.replace_variable_with, (pyratemp._dontescape, SafeData))
        value = self.replace_variable_with.format(value)
        if safe:
            value = pyratemp._dontescape(value)
        return value


class NameHarvesterTemplate(TemplateWithDefaultFallback):

    def evalfunc(self, expr, variables):
        self.harvested_variable_names.add(expr)
        return super(NameHarvesterTemplate, self).evalfunc(expr, variables)

    def render(self, context):
        self.harvested_variable_names = set()
        return super(NameHarvesterTemplate, self).render(context)

    def harvest_names(self):
        self.render({})
        return self.harvested_variable_names


def get_variable_names_from_template(template):
    template = NameHarvesterTemplate(template.text, template.id)
    return template.harvest_names()


def format_range(values):
    """Attempts to present the given range in a human-readable format

    >>> format_range([1])
    '1'
    >>> format_range([1,2,3])
    '1:3'
    >>> format_range([1,2,3,5,6])
    '1:3,5:6'
    >>> format_range([1,2,3,5,6,8,10])
    '1:3,5:6,8,10'
    """

    if hasattr(values, "values_list"):
        # We need iter here because values_list returns a special QuerySet
        # object, not a list.
        return format_range(iter(values.values_list("number", flat=True)))

    values = iter(values)
    beginning = end = None

    def _iter(values):
        # We use _iter here to mutate values
        prev = values.next()
        while True:
            try:
                current = values.next()
            except StopIteration, e:
                yield prev, None
                raise e
            yield prev, current
            prev = current

    for prev, current in _iter(values):
        if not beginning:
            beginning = end = prev
        if current:
            if prev == current - 1:
                end = current
            else:
                break
    if beginning is not None:
        if beginning == end:
            this_range = str(beginning)
        else:
            this_range = "{}:{}".format(beginning, end)
        extra = format_range(chain([current], values))
        if extra:
            return "{},{}".format(this_range, extra)
        else:
            return this_range


QR_CODE_URL_TEMPLATE = ("https://chart.googleapis.com/chart?cht=qr"
                        "&chs={size}x{size}&chl={data}&chId=L|2")


def qr_code_from_url(relative_url, request=None, size=500):
    if request:
        url = request.build_absolute_uri(relative_url)
    else:
        url = relative_url
    return QR_CODE_URL_TEMPLATE.format(size=size, data=urllib.quote_plus(url))
