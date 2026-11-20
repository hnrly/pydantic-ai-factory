import Link from "next/link";
import { logInAction } from "../account-actions";

type Props = { searchParams: Promise<{ error?: string }> };

export default async function LogInPage({ searchParams }: Props) {
  const { error } = await searchParams;
  return (
    <>
      <h2>Log in</h2>
      {error && <p className="error">{error}</p>}
      <form action={logInAction}>
        <label>
          Email
          <input name="email" type="email" required autoComplete="email" />
        </label>
        <label>
          Password
          <input name="password" type="password" required autoComplete="current-password" />
        </label>
        <button type="submit">Log in</button>
      </form>
      <p>
        New here? <Link href="/signup">Sign up</Link>.
      </p>
    </>
  );
}
