#!/usr/bin/env python
##########################################################################
#
# Copyright 2008-2012 Jose Fonseca
# All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
##########################################################################/


import sys
import optparse
import re


class MatchObject:

    def __init__(self):
        self.params = {}


class Matcher:

    def match(self, value, mo):
        raise NotImplementedError

    def _matchSequence(self, refValues, srcValues, mo):
        if not isinstance(srcValues, (list, tuple)):
            return False

        if len(refValues) != len(srcValues):
            return False

        for refValue, srcValue in zip(refValues, srcValues):
            if not refValue.match(srcValue, mo):
                return False
        return True

    def __str__(self):
        raise NotImplementerError

    def __repr__(self):
        return str(self)


class WildcardMatcher(Matcher):

    def __init__(self, name = ''):
        self.name = name

    def match(self, value, mo):
        if self.name:
            try:
                refValue = mo.params[self.name]
            except KeyError:
                mo.params[self.name] = value
            else:
                return refValue == value
        return True

    def __str__(self):
        return '<' + self.name + '>'


class LiteralMatcher(Matcher):

    def __init__(self, refValue):
        self.refValue = refValue

    def match(self, value, mo):
        return self.refValue == value

    def __str__(self):
        return repr(self.refValue)


class ApproxMatcher(Matcher):

    def __init__(self, refValue, tolerance = 2**-23):
        self.refValue = refValue
        self.tolerance = tolerance

    def match(self, value, mo):
        if not isinstance(value, float):
            return 

        error = abs(self.refValue - value)
        if self.refValue:
            error = error / self.refValue
        return error <= self.tolerance

    def __str__(self):
        return repr(self.refValue)


class BitmaskMatcher(Matcher):

    def __init__(self, refElements):
        self.refElements = refElements

    def match(self, value, mo):
        return self._matchSequence(self.refElements, value, mo)

    def __str__(self):
        return ' | '.join(map(str, self.refElements))


class ArrayMatcher(Matcher):

    def __init__(self, refElements):
        self.refElements = refElements

    def match(self, value, mo):
        return self._matchSequence(self.refElements, value, mo)

    def __str__(self):
        return '{' + ', '.join(map(str, self.refElements)) + '}'


class StructMatcher(Matcher):

    def __init__(self, refMembers):
        self.refMembers = refMembers

    def match(self, value, mo):
        if not isinstance(value, dict):
            return False

        if len(value) != len(self.refMembers):
            return False

        for name, refMember in self.refMembers.iteritems():
            try:
                member = value[name]
            except KeyError:
                return False
            else:
                if not refMember.match(member, mo):
                    return False

        return True

    def __str__(self):
        return '{' + ', '.join(['%s = %s' % refMember for refMember in self.refMembers.iteritems()]) + '}'


class CallMatcher(Matcher):

    def __init__(self, callNo, functionName, args, ret):
        self.callNo = callNo
        self.functionName = functionName
        self.args = args
        self.ret = ret

    def match(self, call, mo):
        callNo, srcFunctionName, srcArgs, srcRet = call

        if self.functionName != srcFunctionName:
            return False

        refArgs = [value for name, value in self.args]
        srcArgs = [value for name, value in srcArgs]

        if not self._matchSequence(refArgs, srcArgs, mo):
            return False

        if self.ret is None:
            if srcRet is not None:
                return False
        else:
            if not self.ret.match(srcRet, mo):
                return False

        if self.callNo is not None:
            if not self.callNo.match(callNo, mo):
                return False

        return True

    def __str__(self):
        s = self.functionName
        s += '(' + ', '.join(['%s = %s' % refArg for refArg in self.args]) + ')'
        if self.ret is not None:
            s += ' = ' + str(self.ret)
        return s


class TraceMatcher:

    def __init__(self, calls):
        self.calls = calls

    def match(self, trace):
        mo = MatchObject()
        srcCalls = iter(trace.calls)
        for refCall in self.calls:
            skippedSrcCalls = []
            while True:
                try:
                    srcCall = srcCalls.next()
                except StopIteration:
                    if skippedSrcCalls:
                        raise Exception('missing call `%s` (found `%s`)' % (refCall, skippedSrcCalls[0]))
                    else:
                        raise Exception('missing call %s' % refCall)
                if refCall.match(srcCall, mo):
                    break
                else:
                    skippedSrcCalls.append(srcCall)
        return mo

    def __str__(self):
        return ''.join(['%s\n' % call for call in self.calls])


#######################################################################

EOF = -1
SKIP = -2


class ParseError(Exception):

    def __init__(self, msg=None, filename=None, line=None, col=None):
        self.msg = msg
        self.filename = filename
        self.line = line
        self.col = col

    def __str__(self):
        return ':'.join([str(part) for part in (self.filename, self.line, self.col, self.msg) if part != None])
        

class Scanner:
    """Stateless scanner."""

    # should be overriden by derived classes
    tokens = []
    symbols = {}
    literals = {}
    ignorecase = False

    def __init__(self):
        flags = re.DOTALL
        if self.ignorecase:
            flags |= re.IGNORECASE
        self.tokens_re = re.compile(
            '|'.join(['(' + regexp + ')' for type, regexp, test_lit in self.tokens]),
             flags
        )

    def next(self, buf, pos):
        if pos >= len(buf):
            return EOF, '', pos
        mo = self.tokens_re.match(buf, pos)
        if mo:
            text = mo.group()
            type, regexp, test_lit = self.tokens[mo.lastindex - 1]
            pos = mo.end()
            if test_lit:
                type = self.literals.get(text, type)
            return type, text, pos
        else:
            c = buf[pos]
            return self.symbols.get(c, None), c, pos + 1


class Token:

    def __init__(self, type, text, line, col):
        self.type = type
        self.text = text
        self.line = line
        self.col = col


class Lexer:

    # should be overriden by derived classes
    scanner = None
    tabsize = 8

    newline_re = re.compile(r'\r\n?|\n')

    def __init__(self, buf = None, pos = 0, filename = None, fp = None):
        if fp is not None:
            try:
                fileno = fp.fileno()
                length = os.path.getsize(fp.name)
                import mmap
            except:
                # read whole file into memory
                buf = fp.read()
                pos = 0
            else:
                # map the whole file into memory
                if length:
                    # length must not be zero
                    buf = mmap.mmap(fileno, length, access = mmap.ACCESS_READ)
                    pos = os.lseek(fileno, 0, 1)
                else:
                    buf = ''
                    pos = 0

            if filename is None:
                try:
                    filename = fp.name
                except AttributeError:
                    filename = None

        self.buf = buf
        self.pos = pos
        self.line = 1
        self.col = 1
        self.filename = filename

    def next(self):
        while True:
            # save state
            pos = self.pos
            line = self.line
            col = self.col

            type, text, endpos = self.scanner.next(self.buf, pos)
            assert pos + len(text) == endpos
            self.consume(text)
            type, text = self.filter(type, text)
            self.pos = endpos

            if type == SKIP:
                continue
            elif type is None:
                msg = 'unexpected char '
                if text >= ' ' and text <= '~':
                    msg += "'%s'" % text
                else:
                    msg += "0x%X" % ord(text)
                raise ParseError(msg, self.filename, line, col)
            else:
                break
        return Token(type = type, text = text, line = line, col = col)

    def consume(self, text):
        # update line number
        pos = 0
        for mo in self.newline_re.finditer(text, pos):
            self.line += 1
            self.col = 1
            pos = mo.end()

        # update column number
        while True:
            tabpos = text.find('\t', pos)
            if tabpos == -1:
                break
            self.col += tabpos - pos
            self.col = ((self.col - 1)//self.tabsize + 1)*self.tabsize + 1
            pos = tabpos + 1
        self.col += len(text) - pos


class Parser:

    def __init__(self, lexer):
        self.lexer = lexer
        self.lookahead = self.lexer.next()

    def match(self, type):
        return self.lookahead.type == type

    def skip(self, type):
        while not self.match(type):
            self.consume()

    def error(self):
        raise ParseError(
            msg = 'unexpected token %r' % self.lookahead.text, 
            filename = self.lexer.filename, 
            line = self.lookahead.line, 
            col = self.lookahead.col)

    def consume(self, type = None):
        if type is not None and not self.match(type):
            self.error()
        token = self.lookahead
        self.lookahead = self.lexer.next()
        return token


#######################################################################

ID, NUMBER, HEXNUM, STRING, WILDCARD, PRAGMA, LPAREN, RPAREN, LCURLY, RCURLY, COMMA, AMP, EQUAL, VERT, BLOB = xrange(15)


class CallScanner(Scanner):

    # token regular expression table
    tokens = [
        # whitespace
        (SKIP, r'[ \t\f\r\n\v]+', False),

        # Alphanumeric IDs
        (ID, r'[a-zA-Z_][a-zA-Z0-9_]*(?:::[a-zA-Z_][a-zA-Z0-9_]*)?', True),

        # Numeric IDs
        (HEXNUM, r'-?0x[0-9a-fA-F]+', False),
        
        # Numeric IDs
        (NUMBER, r'-?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+][0-9]+)?', False),

        # String IDs
        (STRING, r'"[^"\\]*(?:\\.[^"\\]*)*"', False),
        
        # Wildcard
        (WILDCARD, r'<[^>]*>', False),
        
        # Pragma
        (PRAGMA, r'#[^\r\n]*', False),
    ]

    # symbol table
    symbols = {
        '(': LPAREN,
        ')': RPAREN,
        '{': LCURLY,
        '}': RCURLY,
        ',': COMMA,
        '&': AMP,
        '=': EQUAL,
        '|': VERT,
    }

    # literal table
    literals = {
        'blob': BLOB
    }


class CallLexer(Lexer):

    scanner = CallScanner()

    def filter(self, type, text):
        if type == STRING:
            text = text[1:-1]

            # line continuations
            text = text.replace('\\\r\n', '')
            text = text.replace('\\\r', '')
            text = text.replace('\\\n', '')
            
            # quotes
            text = text.replace('\\"', '"')

            type = ID

        return type, text


class TraceParser(Parser):

    def __init__(self, stream):
        lexer = CallLexer(fp = stream)
        Parser.__init__(self, lexer)

    def eof(self):
        return self.match(EOF)

    def parse(self):
        while not self.eof():
            self.parse_element()
        return TraceMatcher(self.calls)

    def parse_element(self):
        if self.lookahead.type == PRAGMA:
            # TODO
            token = self.consume()
            self.handlePragma(token.text)
        else:
            self.parse_call()

    def parse_call(self):
        while self.lookahead.type == PRAGMA:
            # TODO
            token = self.consume()

        if self.lookahead.type == NUMBER:
            token = self.consume()
            callNo = self.handleInt(int(token.text))
        elif self.lookahead.type == WILDCARD:
            token = self.consume()
            callNo = self.handleWildcard((token.text[1:-1]))
        else:
            callNo = None
        
        functionName = self.consume(ID).text

        args = self.parse_sequence(LPAREN, RPAREN, self.parse_pair)

        if self.match(EQUAL):
            self.consume(EQUAL)
            ret = self.parse_value()
        else:
            ret = None

        self.handleCall(callNo, functionName, args, ret)

    def parse_pair(self):
        '''Parse a `name = value` pair.'''
        name = self.consume(ID).text
        self.consume(EQUAL)
        value = self.parse_value()
        return name, value

    def parse_opt_pair(self):
        '''Parse an optional `name = value` pair.'''
        if self.match(ID):
            name = self.consume(ID).text
            if self.match(EQUAL):
                self.consume(EQUAL)
                value = self.parse_value()
            else:
                value = name
                name = None
        else:
            name = None
            value = self.parse_value()
        if name is None:
            return value
        else:
            return name, value

    def parse_value(self):
        value = self._parse_value()
        if self.match(VERT):
            flags = [value]
            while self.match(VERT):
                self.consume()
                value = self._parse_value()
                flags.append(value)
            return self.handleBitmask(flags)
        else:
            return value

    def _parse_value(self):
        if self.match(AMP):
            self.consume()
            value = [self.parse_value()]
            return self.handleArray(value)
        elif self.match(ID):
            token = self.consume()
            value = token.text
            return self.handleID(value)
        elif self.match(STRING):
            token = self.consume()
            value = token.text
            return self.handleString(value)
        elif self.match(NUMBER):
            token = self.consume()
            value = float(token.text)
            return self.handleFloat(value)
        elif self.match(HEXNUM):
            token = self.consume()
            value = int(token.text, 16)
            return self.handleInt(value)
        elif self.match(LCURLY):
            value = self.parse_sequence(LCURLY, RCURLY, self.parse_opt_pair)
            if len(value) and isinstance(value[0], tuple):
                value = dict(value)
                return self.handleStruct(value)
            else:
                return self.handleArray(value)
        elif self.match(BLOB):
            token = self.consume()
            self.consume(LPAREN)
            token = self.consume()
            length = int(token.text)
            self.consume(RPAREN)
            return self.handleBlob(length)
        elif self.match(WILDCARD):
            token = self.consume()
            return self.handleWildcard(token.text[1:-1])
        else:
            self.error()

    def parse_sequence(self, ltype, rtype, elementParser):
        '''Parse a comma separated list'''

        elements = []

        self.consume(ltype)
        sep = None
        while not self.match(rtype):
            if sep is None:
                sep = COMMA
            else:
                self.consume(sep)
            element = elementParser()
            elements.append(element)
        self.consume(rtype)

        return elements
    
    def handleID(self, value):
        raise NotImplementedError

    def handleInt(self, value):
        raise NotImplementedError

    def handleFloat(self, value):
        raise NotImplementedError

    def handleString(self, value):
        raise NotImplementedError

    def handleBitmask(self, value):
        raise NotImplementedError

    def handleArray(self, value):
        raise NotImplementedError

    def handleStruct(self, value):
        raise NotImplementedError

    def handleBlob(self, length):
        return self.handleID('blob(%u)' % length)

    def handleWildcard(self, name):
        raise NotImplementedError

    def handleCall(self, callNo, functionName, args, ret):
        raise NotImplementedError

    def handlePragma(self, line):
        pass


class RefTraceParser(TraceParser):

    def __init__(self, stream):
        TraceParser.__init__(self, stream)
        self.calls = []

    def parse(self):
        TraceParser.parse(self)
        return TraceMatcher(self.calls)

    def handleID(self, value):
        return LiteralMatcher(value)

    def handleInt(self, value):
        return LiteralMatcher(value)

    def handleFloat(self, value):
        return ApproxMatcher(value)

    def handleString(self, value):
        return LiteralMatcher(value)

    def handleBitmask(self, value):
        return BitmaskMatcher(value)

    def handleArray(self, value):
        return ArrayMatcher(value)

    def handleStruct(self, value):
        return StructMatcher(value)

    def handleWildcard(self, name):
        return WildcardMatcher(name)

    def handleCall(self, callNo, functionName, args, ret):
        call = CallMatcher(callNo, functionName, args, ret)
        self.calls.append(call)


class SrcTraceParser(TraceParser):

    def __init__(self, stream):
        TraceParser.__init__(self, stream)
        self.calls = []

    def parse(self):
        TraceParser.parse(self)
        return TraceMatcher(self.calls)

    def handleID(self, value):
        return value

    def handleInt(self, value):
        return int(value)

    def handleFloat(self, value):
        return float(value)

    def handleString(self, value):
        return value

    def handleBitmask(self, value):
        return value

    def handleArray(self, elements):
        return list(elements)

    def handleStruct(self, members):
        return dict(members)

    def handleCall(self, callNo, functionName, args, ret):
        call = (callNo, functionName, args, ret)
        self.calls.append(call)


def main():
    # Parse command line options
    optparser = optparse.OptionParser(
        usage='\n\t%prog [OPTIONS] REF_TRACE SRC_TRACE',
        version='%%prog')
    optparser.add_option(
        '-v', '--verbose',
        action="store_true",
        dest="verbose", default=False,
        help="verbose output")
    (options, args) = optparser.parse_args(sys.argv[1:])

    if len(args) != 2:
        optparser.error('wrong number of arguments')

    refParser = RefTraceParser(open(args[0], 'rt'))
    refTrace = refParser.parse()
    sys.stdout.write(str(refTrace))
    srcParser = SrcTraceParser(open(args[1], 'rt'))
    srcTrace = srcParser.parse()
    mo = refTrace.match(srcTrace)

    paramNames = mo.params.keys()
    paramNames.sort()
    for paramName in paramNames:
        print '%s = %r' % (paramName, mo.params[paramName])


if __name__ == '__main__':
    main()
