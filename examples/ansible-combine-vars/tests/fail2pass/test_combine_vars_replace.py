"""fail2pass — TestVariableUtils::test_combine_vars_replace, the instance's single FAIL_TO_PASS,
lifted from ansible's test/units/utils/test_vars.py AFTER the task's test patch (which adds the
`dict | VarsWithSources` case to combine_vars_replace_data).

On the baseline this raises `TypeError: unsupported operand type(s) for |: 'dict' and
'VarsWithSources'`; it passes once VarsWithSources gains __or__/__ror__/__ior__ (the golden patch).
The only change vs upstream is swapping ansible's `from units.compat import unittest` test shim for
the stdlib `unittest`, so the file runs standalone in a bucket — the data and assertions are unchanged.
"""
import unittest
from collections import defaultdict
from unittest import mock

from ansible.utils.vars import combine_vars
from ansible.vars.manager import VarsWithSources


class TestVariableUtils(unittest.TestCase):
    combine_vars_replace_data = (
        dict(
            a=dict(a=1),
            b=dict(b=2),
            result=dict(a=1, b=2)
        ),
        dict(
            a=dict(a=1),
            b=VarsWithSources().new_vars_with_sources(dict(b=2), dict(b='task vars')),
            result=dict(a=1, b=2),
        ),
        dict(
            a=dict(a=1, c=dict(foo='bar')),
            b=dict(b=2, c=dict(baz='bam')),
            result=dict(a=1, b=2, c=dict(baz='bam'))
        ),
        dict(
            a=defaultdict(a=1, c=dict(foo='bar')),
            b=dict(b=2, c=defaultdict(baz='bam')),
            result=defaultdict(a=1, b=2, c=defaultdict(baz='bam'))
        ),
    )

    def test_combine_vars_replace(self):
        with mock.patch('ansible.constants.DEFAULT_HASH_BEHAVIOUR', 'replace'):
            for test in self.combine_vars_replace_data:
                self.assertEqual(combine_vars(test['a'], test['b']), test['result'])


if __name__ == "__main__":
    unittest.main()
