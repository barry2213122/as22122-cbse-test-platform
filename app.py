import streamlit as st

st.set_page_config(
    page_title="as22122 | CBSE Test Arena",
    page_icon="📚",
    layout="wide"
)

st.title("🎓 as22122")
st.subheader("Your Personal CBSE Class 10 Test Platform")

st.write("Welcome to your examination dashboard!")

st.info("Your personal board exam preparation journey starts here.")

if st.button("Start Your First Test"):
    st.success("Your test platform is getting ready!")
