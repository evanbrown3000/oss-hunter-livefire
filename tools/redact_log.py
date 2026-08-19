import re,sys
t=sys.stdin.read()
for p,r in [(re.compile(r'(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+'),r'\1=[REDACTED]'),(re.compile(r'\bsk-[A-Za-z0-9_-]{12,}\b'),'[REDACTED_API_KEY]')]:t=p.sub(r,t)
sys.stdout.write(t)
