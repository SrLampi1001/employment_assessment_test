/** Discord-like color palette (similar, not equal). */
export const colors = {
  // Brand
  blurple: '#5865F2',
  blurpleHover: '#4752C4',

  // Surfaces (dark theme, per Discord)
  background: '#36393F',
  sidebar: '#2F3136',
  channelRail: '#202225',
  input: '#40444B',
  border: '#202225',

  // Text
  text: '#DCDDDE',
  textMuted: '#72767D',
  textHeader: '#FFFFFF',

  // Status
  danger: '#ED4245',
  success: '#3BA55D',
  warning: '#FAA61A',

  // Message states
  pendingBg: 'rgba(88, 101, 242, 0.05)',
  failedBorder: '#ED4245',
} as const

export const radii = {
  sm: 4,
  md: 8,
  lg: 16,
} as const
