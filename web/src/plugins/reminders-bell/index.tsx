import { registerPlugin } from '@/sdk';
import { RemindersBell } from './RemindersBell';

// Top-level reminders affordance — a bell in the top-right chrome with an
// orb-tinted dot when reminders are scheduled. Lifted out of the Agent
// settings tab so reminders are reachable without opening settings.
registerPlugin({
  id: 'reminders-bell',
  order: 10,
  slots: {
    'overlay-top': RemindersBell,
  },
});
