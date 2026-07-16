"""Password strength policy.

This policy is intentionally **length + blocklist + similarity** based, following
NIST SP 800-63B guidance — NOT forced complexity classes. We do not require a mix
of uppercase / lowercase / digits / symbols, because those rules push users toward
predictable patterns ("Password1!") without meaningfully raising entropy.

Instead we:
  1. enforce a minimum length (8) and the bcrypt 72-byte ceiling,
  2. reject the most common / obvious passwords via a bundled blocklist, and
  3. reject passwords that contain (or are contained in) the user's email.

``check_password_strength`` is a pure function: no I/O, no external dependency.
It is the single source of truth wired into registration, password reset, and
invite-acceptance.
"""

from __future__ import annotations

# The ~200 most common / obvious passwords. Curated inline: no file I/O, no
# external dependency. Kept lowercase; comparison is case-insensitive.
COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        # top leaked numeric / sequential
        "123456", "12345678", "123456789", "1234567890", "1234567", "12345",
        "111111", "000000", "121212", "654321", "123123", "112233", "789456",
        "159753", "987654321", "666666", "888888", "222222", "555555", "777777",
        "999999", "101010", "202020", "123321", "12341234", "147258369",
        "1q2w3e4r", "1q2w3e4r5t", "q1w2e3r4", "1qaz2wsx", "zaq12wsx", "qazwsx",
        # words: password family
        "password", "password1", "password12", "password123", "passw0rd",
        "p@ssword", "p@ssw0rd", "password!", "pass1234", "passwordpassword",
        "letmein", "letmein1", "welcome", "welcome1", "welcome123",
        # keyboard walks
        "qwerty", "qwerty123", "qwerty1", "qwertyuiop", "asdfgh", "asdfghjkl",
        "zxcvbn", "zxcvbnm", "qwer1234", "1234qwer", "azerty", "poiuyt",
        # names / affection
        "iloveyou", "iloveyou1", "iloveyou2", "loveme", "trustno1", "sunshine",
        "princess", "superman", "batman", "spiderman", "pokemon", "michael",
        "michelle", "jennifer", "jessica", "daniel", "andrew", "matthew",
        "joshua", "hannah", "thomas", "charlie", "george", "harley", "robert",
        "ashley", "nicole", "amanda", "jordan", "hunter", "taylor",
        # admin / system
        "admin", "admin1", "admin123", "administrator", "root", "toor",
        "guest", "user", "test", "test123", "changeme", "default", "secret",
        "login", "master", "access", "system", "manager", "welcome!",
        # sports / misc common
        "football", "football1", "baseball", "basketball", "soccer", "hockey",
        "master1", "shadow", "monkey", "monkey1", "dragon", "dragon1", "mustang",
        "harley1", "ranger", "buster", "soccer1", "tigger", "cheese", "computer",
        "internet", "samsung", "google", "chrome", "firefox", "yahoo", "hotmail",
        # phrases
        "whatever", "freedom", "starwars", "money", "hello", "hello1", "hello123",
        "helloworld", "abc123", "abcd1234", "a1b2c3d4", "abcdefg", "abcdefgh",
        "qwe123", "asd123", "zxc123", "111222", "aaaaaa", "aaaa1111",
        "flower", "summer", "winter", "spring", "autumn", "orange", "banana",
        "apple", "apple123", "iphone", "android", "michael1", "jesus", "jesus1",
        "ginger", "chelsea", "diamond", "nascar", "hannah1", "biteme", "matrix",
        # seasonal + years commonly appended
        "summer2023", "summer2024", "spring2024", "winter2024", "password2023",
        "password2024", "welcome2024", "qwerty2024", "admin2024",
        # obvious variants
        "passw0rd1", "p4ssword", "p4ssw0rd", "passw0rd123", "letmein123",
        "changeit", "newpassword", "mypassword", "temppassword", "notpassword",
    }
)


def check_password_strength(password: str, *, email: str | None = None) -> str | None:
    """Return None if the password is acceptable, else a short reason string.

    Rules are applied in order; the first failure wins.
    """
    if len(password) < 8:
        return "Password must be at least 8 characters."

    if len(password.encode("utf-8")) > 72:
        return "Password must be 72 UTF-8 bytes or fewer."

    if password.strip().lower() in COMMON_PASSWORDS:
        return "This password is too common. Choose something less predictable."

    if email:
        local_part = email.split("@", 1)[0].strip().lower()
        pw_lower = password.lower()
        if len(local_part) >= 3 and (local_part in pw_lower or pw_lower in local_part):
            return "Password should not contain your email address."

    return None
