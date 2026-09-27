from cron_task import create_daily_tasks
from cron_rss import main as process_rss_feeds
from cron_calendar import create_daily_calendar
from cron_careers import main as fetch_career_opportunities


def dispatch_crons():

    crons = [
        ("Tasks", create_daily_tasks),
        ("Calendar Template", create_daily_calendar),
        ("RSS Feeds", process_rss_feeds),
        ("Career Opportunities", fetch_career_opportunities),
    ]
    
    for name, cron_func in crons:
        try:
            print(f"[DISPATCHER] Running {name}...")
            cron_func()
            print(f"[DISPATCHER] {name} completed")
        except Exception as e:
            print(f"[DISPATCHER] {name} failed: {e}")



if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    dispatch_crons()