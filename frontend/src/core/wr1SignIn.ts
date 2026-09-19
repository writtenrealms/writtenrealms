export function callbackParameters(query: Record<string, unknown>) {
  const { code, state } = query;
  if (typeof code !== 'string' || typeof state !== 'string'
      || !/^[A-Za-z0-9_-]{43}$/.test(code) || !/^[A-Za-z0-9_-]{43}$/.test(state)) {
    throw new Error('This Alpha sign-in link is invalid. Please start again from Alpha.');
  }
  return { code, state };
}

export function needsAccountSwitch(currentUser: { id?: number }, hasToken: boolean, targetId: number | null) {
  return hasToken && (!currentUser.id || currentUser.id !== targetId);
}

export function worldDestination(value: unknown) {
  if (typeof value !== 'string' || !/^\/worlds\/[1-9][0-9]*$/.test(value)) {
    throw new Error('The destination world is unavailable. Please start again from Alpha.');
  }
  return value;
}
