package com.atuo.sleep.track

/** 서비스와 알림 리스너가 같은 프로세스에서 공유하는 최근 화면 켜짐 시각. */
object ScreenState {
    @Volatile
    var lastScreenOnAt: Long = 0L
}
