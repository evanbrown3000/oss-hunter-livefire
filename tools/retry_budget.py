import argparse
p=argparse.ArgumentParser();p.add_argument("--success-probability",type=float,required=True);p.add_argument("--value-if-success",type=float,required=True);p.add_argument("--cost-per-attempt",type=float,required=True);p.add_argument("--max-attempts",type=int,default=10);a=p.parse_args()
for n in range(1,a.max_attempts+1):
 s=1-(1-a.success_probability)**n;print(f"attempts={n} cumulative_success={s:.4f} expected_value={s*a.value_if_success-n*a.cost_per_attempt:.2f}")
