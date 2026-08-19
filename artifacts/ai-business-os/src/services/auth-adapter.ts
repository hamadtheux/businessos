export type PrototypeUser = {
  id: string;
  name: string;
  email: string;
};

const SESSION_KEY = "ai-business-os:prototype-session";

function readSession(): PrototypeUser | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as PrototypeUser) : null;
  } catch {
    return null;
  }
}

function writeSession(user: PrototypeUser | null) {
  if (user) localStorage.setItem(SESSION_KEY, JSON.stringify(user));
  else localStorage.removeItem(SESSION_KEY);
}

/**
 * Prototype-only auth boundary. Passwords are validated for UX but are never
 * stored. Replace this adapter with the future backend identity provider.
 */
export const authAdapter = {
  getSession: async () => readSession(),
  login: async (email: string, password: string) => {
    if (!email.includes("@") || password.length < 6) {
      throw new Error("Enter a valid email and a password of at least 6 characters.");
    }
    const user = {
      id: `prototype-${email.toLowerCase()}`,
      name: "Alexandra Andria",
      email: email.toLowerCase(),
    };
    writeSession(user);
    return user;
  },
  register: async (name: string, email: string, password: string) => {
    if (name.trim().length < 2) throw new Error("Enter your full name.");
    if (!email.includes("@")) throw new Error("Enter a valid email address.");
    if (password.length < 8) throw new Error("Use at least 8 characters for your password.");
    const user = {
      id: `prototype-${email.toLowerCase()}`,
      name: name.trim(),
      email: email.toLowerCase(),
    };
    writeSession(user);
    return user;
  },
  logout: async () => writeSession(null),
};

