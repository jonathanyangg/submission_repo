"""Parse a user file and require a top-level usr(robot) function.

Run this with the interpreter you want to enforce. Python 2.7 accepts the
syntax the robots can load. Python 3 accepts the syntax the simulator can
import. The file is not executed.
"""

import ast
import sys


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: syntax_check.py FILE\n")
        return 1
    path = argv[1]
    try:
        with open(path, "r") as handle:
            source = handle.read()
    except (IOError, OSError) as err:
        sys.stderr.write("cannot read %s: %s\n" % (path, err))
        return 1
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as err:
        sys.stderr.write("syntax error: %s\n" % err)
        return 1

    found = False
    for node in tree.body:
        if hasattr(ast, "AsyncFunctionDef") and isinstance(node, ast.AsyncFunctionDef):
            if node.name == "usr":
                sys.stderr.write("usr cannot be async; the robots run Python 2.7\n")
                return 1
        if isinstance(node, ast.FunctionDef) and node.name == "usr":
            found = True
            if len(node.args.args) < 1:
                sys.stderr.write("usr must take the robot argument\n")
                return 1
    if not found:
        sys.stderr.write("missing top-level def usr(robot)\n")
        return 2
    sys.stdout.write("ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
