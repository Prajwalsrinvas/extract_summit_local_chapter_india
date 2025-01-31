import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

# Database configuration
DB_PATH = "nike_tracker.db"

# Page configuration
st.set_page_config(page_title="Nike Price Tracker", page_icon="👟", layout="wide")


@st.cache_data
def load_all_data():
    """Load all data from database into memory"""
    conn = sqlite3.connect(DB_PATH)

    # Load products
    products_df = pd.read_sql_query("SELECT * FROM products", conn)

    # Load price history
    price_history_df = pd.read_sql_query("SELECT * FROM price_history", conn)

    conn.close()

    # Calculate latest prices for each product
    latest_prices = (
        price_history_df.sort_values("timestamp")
        .groupby("product_code")
        .last()
        .reset_index()[["product_code", "price", "timestamp"]]
        .rename(columns={"price": "current_price", "timestamp": "last_updated"})
    )

    # Merge products with their latest prices
    products_with_prices = products_df.merge(
        latest_prices, on="product_code", how="left"
    )

    return {"products": products_with_prices, "price_history": price_history_df}


@st.cache_data
def get_price_history(_price_history_df, product_code):
    """Get price history for a specific product from memory"""
    return _price_history_df[
        _price_history_df["product_code"] == product_code
    ].sort_values("timestamp")


@st.cache_data
def get_price_stats(price_history):
    """Calculate price statistics"""
    return {
        "highest": price_history["price"].max(),
        "lowest": price_history["price"].min(),
        "average": price_history["price"].mean(),
        "current": price_history["price"].iloc[-1],
    }


@st.dialog("Price History", width="large")
def show_price_history(product, _price_history_df):
    """Dialog to show price history graph"""
    st.subheader(f"Price History - {product['title']}")

    # Get price history
    price_history = get_price_history(_price_history_df, product["product_code"])
    stats = get_price_stats(price_history)

    # Show price statistics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Highest", f"₹{stats['highest']:,.2f}")
    with col2:
        st.metric("Lowest", f"₹{stats['lowest']:,.2f}")
    with col3:
        st.metric("Average", f"₹{stats['average']:,.2f}")

    # Show price history graph
    if not price_history.empty:
        fig = px.line(price_history, x="timestamp", y="price", height=400, markers=True)
        fig.update_layout(xaxis_title="Date", yaxis_title="Price (₹)", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.write("No price history available")


@st.cache_data
def filter_products(
    _products_df, search_query=None, category=None, sort_by=None, limit=100
):
    """Filter products from memory"""
    df = _products_df.copy()

    # Apply filters
    if search_query:
        search_query = search_query.lower()
        df = df[
            df["title"].str.lower().str.contains(search_query)
            | df["product_code"].str.lower().str.contains(search_query)
        ]

    if category and category != "All":
        df = df[df["category"] == category]

    # Ensure price is numeric
    df["current_price"] = pd.to_numeric(df["current_price"], errors="coerce")

    # Apply sorting
    if sort_by == "Price: High to Low":
        df = df.sort_values("current_price", ascending=False)
    elif sort_by == "Price: Low to High":
        df = df.sort_values("current_price", ascending=True)
    elif sort_by == "Name: A-Z":
        df = df.sort_values("title", ascending=True)
    elif sort_by == "Name: Z-A":
        df = df.sort_values("title", ascending=False)
    else:  # Recently Updated
        df = df.sort_values("last_updated", ascending=False)
    df.reset_index(inplace=True)
    return df.head(limit)


def main():
    # Load all data into memory at startup
    data = load_all_data()
    products_df = data["products"]
    price_history_df = data["price_history"]

    st.header("Nike Price Tracker 👟")

    # Sidebar filters
    st.sidebar.subheader("Filters")

    # Get unique categories
    categories = ["All"] + sorted(
        products_df["category"].unique().tolist(), reverse=True
    )

    search_query = st.sidebar.text_input("Search products by name or code")
    selected_category = st.sidebar.selectbox("Category", categories)

    sort_options = [
        "Recently Updated",
        "Price: High to Low",
        "Price: Low to High",
        "Name: A-Z",
        "Name: Z-A",
    ]
    sort_by = st.sidebar.selectbox("Sort by", sort_options)

    # Filter products
    filtered_products = filter_products(
        products_df, search_query, selected_category, sort_by
    )

    if filtered_products.empty:
        st.warning("No products found.")
        return

    grid_size = 4

    # Display products in grid
    cols = st.columns(grid_size)
    for idx, row in filtered_products.iterrows():
        # Skip products without images
        if pd.isna(row["image_url"]) or not row["image_url"]:
            continue

        col = cols[idx % grid_size]
        with col:
            st.image(
                row["image_url"],
                use_container_width=True,
                caption=f"₹{row['current_price']:,.2f}",
            )

            # Product title with link
            st.page_link(row["url"], label=f'**{row["title"]}**', icon="🔗")

            # subtitle and chart emoji in the same line
            subtitle_col, chart_col = st.columns([4, 1])
            with subtitle_col:
                st.write(f'`{row["subtitle"]}`')
            with chart_col:
                if st.button(
                    "📈",
                    key=f"chart_{idx}",
                ):
                    show_price_history(row, price_history_df)


if __name__ == "__main__":
    main()
