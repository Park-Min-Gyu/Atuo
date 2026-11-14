package com.atuo.sleep.track

import android.app.KeyguardManager
import android.app.Notification
import android.content.Context
import android.os.PowerManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.atuo.sleep.core.EventType
import com.atuo.sleep.data.EventStore
import com.atuo.sleep.data.Prefs

/**
 * 알림이 "언제" 왔는지만 기록한다 (앱 이름·내용은 읽지도 저장하지도 않는다).
 * 화면이 켜진 시각과 겹치면 그 화면 켜짐은 사용자가 아니라 알림 때문으로 판정된다.
 */
class NotificationSignalService : NotificationListenerService() {
    private var lastRecorded = 0L

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName == packageName) return
        // 음악·다운로드처럼 계속 갱신되는 알림은 화면을 깨우지 않으므로 제외 (전화·알람은 포함)
        val category = sbn.notification?.category
        if (sbn.isOngoing && category != Notification.CATEGORY_CALL && category != Notification.CATEGORY_ALARM) return
        if (!Prefs(this).trackingEnabled) return

        val now = System.currentTimeMillis()
        if (now - lastRecorded < 2000) return
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        val keyguard = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        // 사용자가 폰을 쓰고 있는 동안의 알림은 화면 켜짐과 무관하므로 기록하지 않는다.
        val relevant = !power.isInteractive || keyguard.isKeyguardLocked || now - ScreenState.lastScreenOnAt < 3000
        if (!relevant) return
        lastRecorded = now
        EventStore.get(this).add(EventType.NOTIFICATION, now)
    }
}
