/**
 * Жива ли сессия — когда поток событий закрылся и непонятно почему.
 *
 * 401 и 403 значат «вышел»: переподключаться незачем. Всё остальное
 * (обрыв сети, перезапуск сервера) ничего о сессии не говорит — поток
 * переподключится, когда сервер вернётся.
 */

import * as api from '../../api/auth';
import { ApiFailure } from '../../api/errors';

export { startLive, stopLive } from './events';

export async function probeSession(): Promise<'signed-in' | 'signed-out' | 'unknown'> {
  try {
    await api.currentUser();
    return 'signed-in';
  } catch (error) {
    if (error instanceof ApiFailure && (error.kind === 'credentials' || error.kind === 'session')) {
      return 'signed-out';
    }
    return 'unknown';
  }
}
