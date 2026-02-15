def get_term_code(term_name: str) -> str:
    """
    Map term names to their corresponding code for URL generation.
    Based on patterns: Semester 1 -> 20, Semester 2 -> 25.
    Future mappings (Trimesters, Summer/Winter) can be added here.
    """
    mapping = {
        "Semester 1": "20",
        "Semester 2": "25",
    }
    return mapping.get(term_name)
