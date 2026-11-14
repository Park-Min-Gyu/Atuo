package com.atuo.sleep.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.atuo.sleep.core.AwakeningType
import com.atuo.sleep.core.Fmt
import com.atuo.sleep.core.SleepConfig
import com.atuo.sleep.core.SleepNight
import com.atuo.sleep.core.WeeklyReport
import com.atuo.sleep.data.Analysis
import com.atuo.sleep.data.Permissions
import com.atuo.sleep.data.Prefs
import com.atuo.sleep.data.SleepRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.ZoneId
import kotlin.math.abs

class ScreenActions(
    val setTracking: (Boolean) -> Unit,
    val requestNotifications: () -> Unit,
    val openNotificationAccess: () -> Unit,
    val openUsageAccess: () -> Unit,
    val openBatterySettings: () -> Unit,
    val setGoalMinutes: (Int) -> Unit,
    val setNightOnly: (Boolean) -> Unit,
    val setDemo: (Boolean) -> Unit,
    val clearData: () -> Unit,
)

private val Night = Color(0xFF0E1325)
private val Card = Color(0xFF1A2140)
private val Track = Color(0xFF262E55)
private val Moon = Color(0xFFF4D58D)
private val Sleep = Color(0xFF9FA8FF)
private val TextMain = Color(0xFFEDEFFB)
private val Muted = Color(0xFF959DC2)
private val Warn = Color(0xFFFF9C8A)
private val Good = Color(0xFF7BD8A6)

private class PermissionState(
    val notifications: Boolean,
    val notificationAccess: Boolean,
    val usageAccess: Boolean,
    val battery: Boolean,
)

@Composable
fun SleepScreen(refresh: Int, actions: ScreenActions) {
    val context = LocalContext.current
    val prefs = remember { Prefs(context) }
    var analysis by remember { mutableStateOf<Analysis?>(null) }
    var permissions by remember { mutableStateOf<PermissionState?>(null) }

    LaunchedEffect(refresh) {
        permissions = PermissionState(
            notifications = Permissions.canPostNotifications(context),
            notificationAccess = Permissions.notificationAccess(context),
            usageAccess = Permissions.usageAccess(context),
            battery = Permissions.batteryUnrestricted(context),
        )
        analysis = withContext(Dispatchers.IO) { SleepRepository.load(context) }
    }

    MaterialTheme(colorScheme = darkColorScheme(primary = Moon, onPrimary = Night, background = Night, surface = Card)) {
        Column(
            Modifier
                .fillMaxSize()
                .background(Night)
                .systemBarsPadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // refresh 를 읽어 설정값이 바뀔 때 다시 그려지게 한다
            val tracking = remember(refresh) { prefs.trackingEnabled }
            val goal = remember(refresh) { prefs.goalMinutes }
            val nightOnly = remember(refresh) { prefs.nightOnly }
            val demo = remember(refresh) { prefs.demo }

            Header(tracking, actions.setTracking)
            permissions?.let { PermissionCard(it, actions) }

            val a = analysis
            if (a == null) {
                Text("불러오는 중…", color = Muted)
            } else {
                if (a.demo) Banner("데모 데이터를 보고 있어요. 실제 기록은 아래 설정에서 데모를 끄면 보여요.")
                val report = a.report
                if (report == null) {
                    EmptyCard(tracking)
                } else {
                    ReportCard(report, goal)
                    ChartCard(report, a.zone)
                    InsightCard(report)
                    NightListCard(report, a.zone)
                }
            }
            SettingsCard(goal, nightOnly, demo, actions)
            HowItWorksCard()
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun Header(tracking: Boolean, setTracking: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("수면 패턴", color = TextMain, fontSize = 26.sp, fontWeight = FontWeight.Bold)
            Text(
                if (tracking) "폰을 끄고 켜는 시각을 기록하고 있어요" else "기록이 꺼져 있어요",
                color = if (tracking) Muted else Warn,
                fontSize = 13.sp,
            )
        }
        Switch(
            checked = tracking,
            onCheckedChange = setTracking,
            colors = SwitchDefaults.colors(checkedTrackColor = Moon, checkedThumbColor = Night),
        )
    }
}

@Composable
private fun CardBox(title: String? = null, content: @Composable () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(Card, RoundedCornerShape(18.dp))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (title != null) Text(title, color = TextMain, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        content()
    }
}

@Composable
private fun Banner(text: String) {
    Text(
        text,
        color = Night,
        fontSize = 13.sp,
        modifier = Modifier
            .fillMaxWidth()
            .background(Moon, RoundedCornerShape(12.dp))
            .padding(horizontal = 14.dp, vertical = 10.dp),
    )
}

@Composable
private fun PermissionCard(p: PermissionState, actions: ScreenActions) {
    if (p.notifications && p.notificationAccess && p.usageAccess && p.battery) return
    CardBox("정확도를 높이는 설정") {
        if (!p.notifications) {
            PermissionRow("알림 표시", "주간 리포트 알림을 받으려면 필요해요", actions.requestNotifications)
        }
        if (!p.battery) {
            PermissionRow("배터리 최적화 제외", "밤새 기록이 끊기지 않게 해요 (권장)", actions.openBatterySettings)
        }
        if (!p.notificationAccess) {
            PermissionRow(
                "알림 접근",
                "알림이 온 시각만 확인해 '알림으로 켜진 화면'을 더 정확히 걸러내요. 내용은 읽지 않아요 (권장)",
                actions.openNotificationAccess,
            )
        }
        if (!p.usageAccess) {
            PermissionRow("사용 기록 접근", "앱이 꺼져 있던 시간의 기록을 복원해요 (선택)", actions.openUsageAccess)
        }
    }
}

@Composable
private fun PermissionRow(title: String, description: String, onClick: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, color = TextMain, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(description, color = Muted, fontSize = 12.sp, lineHeight = 16.sp)
        }
        Spacer(Modifier.width(8.dp))
        Button(
            onClick = onClick,
            colors = ButtonDefaults.buttonColors(containerColor = Track, contentColor = Moon),
        ) { Text("설정") }
    }
}

@Composable
private fun EmptyCard(tracking: Boolean) {
    CardBox("아직 분석할 수면 기록이 없어요") {
        Text(
            if (tracking) {
                "오늘 밤 평소처럼 폰을 쓰다가 주무세요. 내일 아침 폰을 켜면 첫 기록이 생겨요.\n" +
                    "미리 화면을 보고 싶다면 아래 설정에서 '데모 데이터'를 켜 보세요."
            } else {
                "위 스위치를 켜면 기록을 시작해요."
            },
            color = Muted,
            fontSize = 14.sp,
            lineHeight = 20.sp,
        )
    }
}

@Composable
private fun ReportCard(r: WeeklyReport, goalMinutes: Int) {
    CardBox {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("주간 리포트", color = TextMain, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    "${Fmt.dayLabel(r.from)} ~ ${Fmt.dayLabel(r.to)} · ${r.nights.size}일 기록",
                    color = Muted,
                    fontSize = 12.sp,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    "${r.score}",
                    color = when {
                        r.score >= 80 -> Good
                        r.score >= 60 -> Moon
                        else -> Warn
                    },
                    fontSize = 40.sp,
                    fontWeight = FontWeight.Bold,
                )
                Text("수면 점수", color = Muted, fontSize = 11.sp)
            }
        }
        Row(Modifier.fillMaxWidth()) {
            Stat("평균 수면", Fmt.duration(r.avgAsleepMs), Modifier.weight(1f))
            Stat("평균 취침", Fmt.minuteOfDay(r.avgBedMinute), Modifier.weight(1f))
            Stat("평균 기상", Fmt.minuteOfDay(r.avgWakeMinute), Modifier.weight(1f))
        }
        Row(Modifier.fillMaxWidth()) {
            Stat("취침 규칙성", r.bedSdMin?.let { "±${it}분" } ?: "–", Modifier.weight(1f))
            Stat(
                "수면 부족 누적",
                if (r.sleepDebtMs < SleepConfig.MINUTE) "없음" else Fmt.duration(r.sleepDebtMs),
                Modifier.weight(1f),
            )
            Stat(
                "주말 시차",
                r.socialJetlagMin?.let { (if (it >= 0) "+" else "−") + Fmt.duration(abs(it) * SleepConfig.MINUTE) } ?: "–",
                Modifier.weight(1f),
            )
        }
        Text("목표 ${Fmt.duration(goalMinutes * SleepConfig.MINUTE)} 기준", color = Muted, fontSize = 11.sp)
    }
}

@Composable
private fun Stat(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(label, color = Muted, fontSize = 11.sp)
        Text(value, color = TextMain, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** 축: 전날 20:00 → 당일 14:00 (18시간) */
private const val AXIS_HOURS = 18
private const val AXIS_START_HOUR = 20

@Composable
private fun ChartCard(r: WeeklyReport, zone: ZoneId) {
    CardBox("지난 7일") {
        val byDate = r.nights.associateBy { it.date }
        var day = r.from
        while (!day.isAfter(r.to)) {
            ChartRow(day, byDate[day], zone)
            day = day.plusDays(1)
        }
        Row(Modifier.fillMaxWidth()) {
            Spacer(Modifier.width(44.dp))
            Box(Modifier.weight(1f)) {
                Row(Modifier.fillMaxWidth()) {
                    for (label in listOf("20시", "0시", "4시", "8시", "12시")) {
                        Text(label, color = Muted, fontSize = 10.sp, modifier = Modifier.weight(if (label == "12시") 2f else 4f))
                    }
                }
            }
            Spacer(Modifier.width(70.dp))
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Legend(Sleep, "수면")
            Spacer(Modifier.width(14.dp))
            Legend(Warn, "폰 사용으로 깸")
            Spacer(Modifier.width(14.dp))
            Legend(Moon, "잠깐 확인")
        }
    }
}

@Composable
private fun Legend(color: Color, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width(10.dp).height(10.dp).background(color, RoundedCornerShape(3.dp)))
        Spacer(Modifier.width(5.dp))
        Text(label, color = Muted, fontSize = 11.sp)
    }
}

@Composable
private fun ChartRow(date: LocalDate, night: SleepNight?, zone: ZoneId) {
    Row(Modifier.fillMaxWidth().height(26.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(
            "${Fmt.dayOfWeek(date)} ${date.dayOfMonth}",
            color = if (date.dayOfWeek.value >= 6) Moon else Muted,
            fontSize = 12.sp,
            modifier = Modifier.width(44.dp),
        )
        Canvas(Modifier.weight(1f).height(16.dp)) {
            val axisStart = date.minusDays(1).atTime(AXIS_START_HOUR, 0).atZone(zone).toInstant().toEpochMilli()
            val axisMs = AXIS_HOURS * SleepConfig.HOUR
            fun x(t: Long) = ((t - axisStart).toFloat() / axisMs).coerceIn(0f, 1f) * size.width
            val radius = CornerRadius(size.height / 2)
            drawRoundRect(Track, size = size, cornerRadius = radius)
            for (h in listOf(4, 8, 12, 16)) { // 0시, 4시, 8시, 12시 눈금
                val gx = size.width * h / AXIS_HOURS
                drawLine(Night, Offset(gx, 0f), Offset(gx, size.height), strokeWidth = 1.5f)
            }
            if (night != null) {
                val alpha = if (night.lowConfidence) 0.45f else 1f
                val left = x(night.start)
                val right = x(night.end)
                drawRoundRect(
                    Sleep.copy(alpha = alpha),
                    topLeft = Offset(left, 0f),
                    size = Size((right - left).coerceAtLeast(2f), size.height),
                    cornerRadius = radius,
                )
                for (w in night.awakenings) {
                    val wx = x(w.start)
                    if (w.type == AwakeningType.PHONE_USE) {
                        val width = (x(w.end) - wx).coerceAtLeast(4f)
                        drawRect(Warn, topLeft = Offset(wx, 0f), size = Size(width, size.height))
                    } else {
                        drawRect(Moon, topLeft = Offset(wx - 1.5f, 0f), size = Size(3f, size.height))
                    }
                }
            }
        }
        Text(
            night?.let { Fmt.duration(it.asleepMs) } ?: "기록 없음",
            color = if (night != null) TextMain else Muted,
            fontSize = 12.sp,
            textAlign = TextAlign.End,
            modifier = Modifier.width(70.dp),
        )
    }
}

@Composable
private fun InsightCard(r: WeeklyReport) {
    if (r.insights.isEmpty()) return
    CardBox("이번 주 분석") {
        for (line in r.insights) {
            Row {
                Text("•", color = Moon, fontSize = 14.sp, modifier = Modifier.width(14.dp))
                Text(line, color = TextMain, fontSize = 14.sp, lineHeight = 20.sp)
            }
        }
    }
}

@Composable
private fun NightListCard(r: WeeklyReport, zone: ZoneId) {
    CardBox("밤별 기록") {
        for (n in r.nights.reversed()) {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(Fmt.dayLabel(n.date), color = TextMain, fontSize = 14.sp, modifier = Modifier.width(84.dp))
                    Text(
                        "${Fmt.time(n.start, zone)} → ${Fmt.time(n.end, zone)}",
                        color = TextMain,
                        fontSize = 14.sp,
                        modifier = Modifier.weight(1f),
                    )
                    Text(Fmt.duration(n.asleepMs), color = Sleep, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }
                val notes = ArrayList<String>()
                val used = n.awakenings.count { it.type == AwakeningType.PHONE_USE }
                val glances = n.awakenings.size - used
                if (used > 0) notes += "폰 사용으로 깸 ${used}회"
                if (glances > 0) notes += "잠깐 확인 ${glances}회"
                if (n.ignoredWakeCount > 0) notes += "알림으로 화면 켜짐 ${n.ignoredWakeCount}회(무시)"
                if (n.lowConfidence) notes += "기록 공백 있음 · 부정확할 수 있어요"
                if (notes.isNotEmpty()) {
                    Text(
                        notes.joinToString(" · "),
                        color = if (n.lowConfidence) Warn else Muted,
                        fontSize = 12.sp,
                        modifier = Modifier.padding(start = 84.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun SettingsCard(goal: Int, nightOnly: Boolean, demo: Boolean, actions: ScreenActions) {
    CardBox("설정") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("목표 수면 시간", color = TextMain, fontSize = 14.sp, modifier = Modifier.weight(1f))
            TextButton(onClick = { actions.setGoalMinutes((goal - 30).coerceAtLeast(240)) }) { Text("−", fontSize = 20.sp) }
            Text(Fmt.duration(goal * SleepConfig.MINUTE), color = TextMain, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            TextButton(onClick = { actions.setGoalMinutes((goal + 30).coerceAtMost(660)) }) { Text("+", fontSize = 20.sp) }
        }
        SwitchRow("밤잠만 인정", "낮에 폰을 안 본 시간을 수면으로 착각하지 않아요. 야간 근무자는 꺼 주세요.", nightOnly, actions.setNightOnly)
        SwitchRow("데모 데이터", "예시 일주일로 화면을 미리 봐요.", demo, actions.setDemo)
        var confirm by remember { mutableStateOf(false) }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("기록 전체 삭제", color = TextMain, fontSize = 14.sp, modifier = Modifier.weight(1f))
            if (confirm) {
                TextButton(onClick = { confirm = false }) { Text("취소", color = Muted) }
                TextButton(onClick = { confirm = false; actions.clearData() }) { Text("삭제", color = Warn) }
            } else {
                TextButton(onClick = { confirm = true }) { Text("삭제…", color = Warn) }
            }
        }
    }
}

@Composable
private fun SwitchRow(title: String, description: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, color = TextMain, fontSize = 14.sp)
            Text(description, color = Muted, fontSize = 12.sp, lineHeight = 16.sp)
        }
        Spacer(Modifier.width(8.dp))
        Switch(
            checked = checked,
            onCheckedChange = onChange,
            colors = SwitchDefaults.colors(checkedTrackColor = Moon, checkedThumbColor = Night),
        )
    }
}

@Composable
private fun HowItWorksCard() {
    CardBox("어떻게 판단하나요?") {
        val lines = listOf(
            "잠든 시각 = 마지막으로 폰 화면이 꺼진 시각",
            "일어난 시각 = 그 뒤 처음으로 직접 잠금을 풀고 폰을 켠 시각",
            "알림·전화·충전으로 화면만 켜진 것은 잠금 해제가 없으므로 깬 것으로 보지 않아요",
            "자다가 직접 폰을 켜서 쓰면 그 시간은 수면에서 빼고 '깸'으로 기록해요",
            "모든 기록은 이 폰 안에만 저장돼요",
        )
        for (line in lines) {
            Row {
                Text("•", color = Muted, fontSize = 13.sp, modifier = Modifier.width(14.dp))
                Text(line, color = Muted, fontSize = 13.sp, lineHeight = 18.sp)
            }
        }
    }
}
