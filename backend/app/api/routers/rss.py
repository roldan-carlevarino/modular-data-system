from fastapi import APIRouter, HTTPException
import psycopg2
import os

router = APIRouter(prefix="/rss", tags=["RSS"])  

@router.get("/top-global") 
def get_top_global(limit: int = 10):
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT 
                id,
                source_id,
                title,
                link,
                score_ml,
                score_markets,
                score_quant,
                score_politics,
                global_rank,
                top_category,
                category_rank,
                created_at
            FROM rss_articles
            WHERE global_rank IS NOT NULL
            ORDER BY global_rank ASC
            LIMIT %s
        """, (limit,))
        
        rows = cur.fetchall()
        
        articles = []
        for row in rows:
            articles.append({
                "id": row[0],
                "source_id": row[1],
                "title": row[2],
                "link": row[3],
                "scores": {
                    "ml": float(row[4]),
                    "markets": float(row[5]),
                    "quant": float(row[6]),
                    "politics": float(row[7])
                },
                "global_rank": row[8],
                "top_category": row[9],
                "category_rank": row[10],
                "created_at": row[11].isoformat() if row[11] else None
            })
        
        return {
            "total": len(articles),
            "limit": limit,
            "articles": articles
        }
        
    finally:
        cur.close()
        conn.close()


@router.get("/top/{category}")  
def get_top_category(category: str, limit: int = 10):
    valid_categories = ["ml", "markets", "quant", "politics"]
    
    if category not in valid_categories:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid category. Must be one of: {', '.join(valid_categories)}"
        )
    
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT 
                id,
                source_id,
                title,
                link,
                score_ml,
                score_markets,
                score_quant,
                score_politics,
                global_rank,
                top_category,
                category_rank,
                created_at
            FROM rss_articles
            WHERE top_category = %s
            ORDER BY category_rank ASC
            LIMIT %s
        """, (category, limit))
        
        rows = cur.fetchall()
        
        articles = []
        for row in rows:
            articles.append({
                "id": row[0],
                "source_id": row[1],
                "title": row[2],
                "link": row[3],
                "scores": {
                    "ml": float(row[4]),
                    "markets": float(row[5]),
                    "quant": float(row[6]),
                    "politics": float(row[7])
                },
                "global_rank": row[8],
                "top_category": row[9],
                "category_rank": row[10],
                "created_at": row[11].isoformat() if row[11] else None
            })
        
        return {
            "category": category,
            "total": len(articles),
            "limit": limit,
            "articles": articles
        }
        
    finally:
        cur.close()
        conn.close()