import Link from "next/link";
import { MIN_PASSWORD_LENGTH } from "@/lib/accounts";
import { signUpAction } from "../account-actions";

type Props = { searchParams: Promise<{ error?: string }> };

export default async function SignUpPage({ searchParams }: Props) {
  const { error } = await searchParams;
  return (
    <>
      <h2>Sign up</h2>
      {error && <p className="error">{error}</p>}
      <form action={signUpAction}>
        <label>
          Name
          <input name="name" required autoComplete="name" />
        </label>
        <label>
          Email
          <input name="email" type="email" required autoComplete="email" />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            autoComplete="new-password"
          />
        </label>
        <button type="submit">Sign up</button>
      </form>
      <p>
        Already have an account? <Link href="/login">Log in</Link>.
      </p>
    </>
  );
}
