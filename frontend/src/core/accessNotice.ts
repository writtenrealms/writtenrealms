// Only platform admission responses belong in the auth form's quiet notice.
export function accessNotice(error: any): string {
  const response = error?.response;
  const data = response?.data;
  if (response?.status === 403 &&
      ['invite_only', 'site_closed'].includes(data?.code) &&
      typeof data?.detail === 'string') {
    return data.detail.trim();
  }
  return '';
}
