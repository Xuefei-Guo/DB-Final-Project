
// Q12: Top 3 products co-purchased with headphones
// Parameters: None
// Returns: product_id, name, order_count

MATCH (headphone:Product)-[:IN_CATEGORY]->(category:Category)
WHERE category.name = 'headphones'

MATCH (o:Order)-[:CONTAINS]->(headphone)
MATCH (o)-[:CONTAINS]->(candidate:Product)

WHERE candidate.product_id <> headphone.product_id

RETURN
    candidate.product_id AS product_id,
    candidate.name AS name,
    COUNT(DISTINCT o) AS order_count

ORDER BY order_count DESC, product_id ASC
LIMIT 3;
