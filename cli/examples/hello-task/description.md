# Fix the off-by-one in `factorial`

`mathx.core.factorial(n)` should return `n!` — the product of the integers from 1 to n,
with `factorial(0) == 1`.

It currently returns the wrong answer for `n >= 2`: the loop stops one short of `n`, so
`factorial(5)` returns `24` instead of `120`.

**Fix** `factorial` so that `factorial(n) == n!` for every `n >= 0`. Keep the public
interface unchanged (`mathx.core.factorial`).
