# Fix the broken `add` function

`calc.core.add(a, b)` is supposed to return the **sum** of its two arguments, but it currently
returns their **difference**. Fix it so that `add(a, b)` returns `a + b` for all inputs.

Keep the public interface unchanged (`add` stays importable as `calc.add`).
