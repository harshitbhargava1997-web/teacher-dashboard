def save_to_cloud_db(new_df):
    """Dynamically saves new records to Supabase. Handles missing columns automatically."""
    if new_df.empty:
        return
    try:
        # Standardize column names to lowercase and replace spaces/symbols for SQL safety
        new_df.columns = [str(c).strip().lower().replace(" ", "_").replace("(", "").replace(")", "").replace(":", "") for c in new_df.columns]
        
        # Ensure 'full_name' exists for tracking
        if 'fullname' not in new_df.columns:
            if 'firstname' in new_df.columns and 'lastname' in new_df.columns:
                new_df['fullname'] = (new_df['firstname'].fillna('') + " " + new_df['lastname'].fillna('')).str.strip()
            else:
                new_df['fullname'] = 'Unknown Teacher'

        # Write to Supabase dynamically (if_exists='append' will auto-create the table if it's missing)
        new_df.to_sql('master_sessions', engine, if_exists='append', index=False, method='multi')
        st.sidebar.success("Successfully synced into Supabase Cloud DB!")
    except Exception as e:
        st.sidebar.error(f"Database write error: {e}")
