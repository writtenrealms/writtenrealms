import { accessNotice } from '@/core/accessNotice';

export function loginLinkError(error: any): { message: string; retryable: boolean } {
  const response = error?.response;
  if (!response) return {
    message: 'Unable to reach the sign-in service. Check your connection and try again.',
    retryable: true,
  };
  if (response.status >= 500) return {
    message: 'The sign-in service is temporarily unavailable. Please try again shortly.',
    retryable: true,
  };
  if (response.status === 429) return {
    message: 'Too many sign-in attempts. Please wait a moment and try again.',
    retryable: true,
  };
  const data = response.data;
  const detail = [accessNotice(error), data?.token?.[0], data?.non_field_errors?.[0], data?.detail]
    .find(value => typeof value === 'string' && value.trim());
  return {
    message: detail || (response.status === 400
      ? 'This login link is invalid or expired. Please request a new one.'
      : 'Sign-in could not be completed. Please request a new login link.'),
    retryable: false,
  };
}
