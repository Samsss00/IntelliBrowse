# cli.py
import argparse
from app.agent.planner import plan_from_query
from app.agent.executor import execute_plan
from app.utils.logger import logger
from app.utils.export import save_results

def main():
    parser = argparse.ArgumentParser(description="Web Navigator AI Agent (local)")
    parser.add_argument("query", type=str, help="e.g., Find top 5 laptops under 50k on Flipkart")
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--site", choices=["flipkart","amazon"], help="Force a site (optional)")
    parser.add_argument("--budget", type=int, help="Override budget in INR (optional)")
    args = parser.parse_args()

    # build plan
    plan = plan_from_query(args.query, args.max)
    if args.site:
        plan["plan"][0]["site"] = args.site
    if args.budget is not None:
        plan["plan"][0]["max_price"] = args.budget
        # also nudge the query to include a clean "under <budget>"
        if "query" in plan["plan"][0] and plan["plan"][0]["query"]:
            q = plan["plan"][0]["query"]
            if "under" not in q.lower():
                plan["plan"][0]["query"] = f"{q} under {args.budget}"

    logger.info(f"PLAN:\n {plan}\n")

    results, artifacts = execute_plan(plan)
    logger.info(f"RESULTS:\n {results}\n")
    logger.info(f"ARTIFACTS:\n {artifacts}\n")

    # Save CSV/JSON next to screenshots
    saved = save_results(artifacts["run_dir"], results)
    logger.info(f"EXPORTED:\n {saved}\n")

if __name__ == "__main__":
    main()
