content = open("full.mlir").read()
import re

pattern = re.compile(
    r'// -----// IR Dump Before (?P<pass_name>.*?) '
    r'\((?P<pass_key>.*?)\) '
    r'\((?P<operation>.*?)\) //----- //\n(?P<body>.*?)(?=(// -----// IR Dump Before|\Z))',
    re.DOTALL
)

item = 'source'

title = 'Python ast_to_ttir'

# 打印每段内容
for idx, match in enumerate(pattern.finditer(content)):
    pass_name = match.group("pass_name").strip()
    pass_key = match.group("pass_key").strip()
    operation = match.group("operation").strip()
    body = match.group("body").strip()

    fp = open(f"{idx+1:02d}-{item}.mlir", "w")
    print(f"// Next run Pass --{pass_key}\n// IR Dump After {title}\n\n{body}", file=fp)
    item = f"{pass_name}"
    title = f"{item} ({operation})\n// Current Run Pass --{pass_key}"

fp = open(f"{idx+2:02d}-{item}.mlir", "w")
print(f"// IR Dump After {title}\n", file=fp)
