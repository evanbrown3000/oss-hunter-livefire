import argparse
p=argparse.ArgumentParser();p.add_argument("--slo",type=float,required=True);p.add_argument("--window-total",type=int,required=True);p.add_argument("--window-bad",type=int,required=True);a=p.parse_args();e=a.window_bad/a.window_total if a.window_total else 0;b=1-a.slo;print(f"observed_error_rate={e:.6f}\nerror_budget={b:.6f}\nburn_rate={(e/b if b else float('inf')):.2f}x")
