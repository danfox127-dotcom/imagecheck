# --- This is the corrected Results Display block ---
                if matches:
                    st.balloons()
                    st.success(f"Found {len(matches)} potential match(es)!")
                    for m in matches:
                        with st.container():
                            col1, col2 = st.columns([1, 4])
                            with col1:
                                st.image(m['Image'], use_container_width=True)
                            with col2:
                                # Fallback to 'Doctor' if 'Name' column isn't found
                                doctor_name = m.get('Name', 'Doctor Profile')
                                st.subheader(doctor_name)
                                st.write(f"🔗 [View Official Profile]({m['URL']})")
                            st.divider()
                else:
                    st.warning("No visual match found in the database. You might try increasing the 'Match Sensitivity' in the sidebar.")
