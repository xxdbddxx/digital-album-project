/**
 * @file lv_voice_assistant.c
 * @brief 情感语音交互视觉控制器
 *
 * @architecture
 * 本文件负责实现语音唤醒后的流式 UI 渲染机制。
 * 1. 动态状态渲染：将后端返回的 ASR/LLM 状态转换为对应的图标与微动画（如呼吸灯、波纹）。
 * 2. 跨屏组件管理：无论是主屏还是照片流转页面，该控制器都负责通过 Layer 机制将唤醒动画强行覆盖在上层。
 * 3. 资源解耦：动画的申请和销毁采用内存池和动态资源机制，防止爆内存。
 */
// 
#include <stdio.h>
#include "lvgl.h"
#include "esp_log.h"
#include <string.h>
#include <stdlib.h>
#include "lv_voice_assistant.h"
#include "lvgl_port.h"

static const char *TAG = "";

/* 导入中文字体 */
extern const lv_font_t lv_font_cjk_16;

/* 全局 UI 对象 */
static lv_obj_t *assistant_layer = NULL;   // 全屏遮罩层（半透明）
static lv_obj_t *dialog_box = NULL;       // 对话卡片容器

/* 动效与气泡关键组件 */
static lv_obj_t *state_title = NULL;       // 顶部状态文本
static lv_obj_t *feedback_container = NULL; // 核心反馈区容器 (120x120)
static lv_obj_t *mic_icon = NULL;          // 居中麦克风图标/提示
static lv_obj_t *loading_arc = NULL;       // 思考中旋转弧圈
static lv_obj_t *ripples[3] = {NULL};      // 涟漪水波纹圆圈
static lv_obj_t *chat_container = NULL;    // 对话区域容器
static lv_obj_t *user_label = NULL;        // 用户问话文本 label
static lv_obj_t *ai_label = NULL;          // AI 答复文本 label

/* 动效与定时器辅助变量 */
static lv_timer_t *typewriter_timer = NULL;
static lv_timer_t *exit_timer = NULL;
static char typewriter_text[1024];
static uint32_t typewriter_index = 0;

/* 前向声明 */
static void lv_va_create_dialog(void);
static void va_stop_ripple_anims(void);
static void va_exit_timer_cb(lv_timer_t *t);

/* ── CJK 字体文本净化器 ──
 * 问题根源：LVGL 字库仅含简体 CJK，当文本含 ASCII 空格(U+20) 或繁体字时，
 * 每帧都会疯狂输出 [Warn] glyph not found 日志洪水，拖垮串口和调度器。
 * 解决方案：在文本送入 lv_label 之前，逐字扫描 UTF-8 序列：
 *   - ASCII 空格(0x20) → 替换为全角空格(U+3000 = \xe3\x80\x80)
 *   - 使用实际字体查询 glyph；字库未收录的字符直接跳过
 */
static void sanitize_for_cjk_font(const char *src, char *dst, size_t dst_size) {
    if (!src || !dst || dst_size < 4) { if (dst) dst[0] = '\0'; return; }
    size_t di = 0;
    const unsigned char *s = (const unsigned char *)src;
    while (*s && di + 4 < dst_size) {
        if (*s == 0x20) {
            // ASCII 空格 → 全角空格 U+3000 (UTF-8: E3 80 80)
            dst[di++] = '\xe3'; dst[di++] = '\x80'; dst[di++] = '\x80';
            s++;
        } else if (*s < 0x80) {
            // ASCII 可打印字符（不含空格）直接保留
            dst[di++] = (char)(*s++);
        } else if ((*s & 0xF0) == 0xE0 && *(s+1) && *(s+2)) {
            // 3字节 UTF-8 → 解码 Unicode 码点
            uint32_t cp = ((uint32_t)(*s & 0x0F) << 12)
                        | ((uint32_t)(*(s+1) & 0x3F) << 6)
                        |  (uint32_t)(*(s+2) & 0x3F);
            /*
             * The generated CJK font contains only a subset of the Unicode
             * CJK block. A range check accepts unsupported Traditional
             * characters and causes LVGL to log glyph-not-found every frame.
             */
            lv_font_glyph_dsc_t glyph_dsc;
            if (lv_font_get_glyph_dsc(&lv_font_cjk_16, &glyph_dsc, cp, 0)) {
                dst[di++] = (char)*s;
                dst[di++] = (char)*(s+1);
                dst[di++] = (char)*(s+2);
            }
            s += 3;
        } else if ((*s & 0xE0) == 0xC0 && *(s+1)) {
            uint32_t cp = ((uint32_t)(*s & 0x1F) << 6)
                        |  (uint32_t)(*(s+1) & 0x3F);
            lv_font_glyph_dsc_t glyph_dsc;
            if (lv_font_get_glyph_dsc(&lv_font_cjk_16, &glyph_dsc, cp, 0)) {
                dst[di++] = (char)*s;
                dst[di++] = (char)*(s+1);
            }
            s += 2;
        } else if ((*s & 0xF8) == 0xF0 && *(s+1) && *(s+2) && *(s+3)) {
            // 4字节 UTF-8（Emoji 等），字库不含，静默跳过
            s += 4;
        } else {
            s++;
        }
    }
    dst[di] = '\0';
}

// 静态净化缓冲区（避免在回调里动态分配，预留前缀空间）
static char s_sanitized_text[960];

static lv_coord_t va_dialog_target_y(void) {
    if (!assistant_layer || !dialog_box) return 0;
    lv_obj_update_layout(assistant_layer);
    lv_coord_t target =
        lv_obj_get_height(assistant_layer) - lv_obj_get_height(dialog_box) - 16;
    return target > 0 ? target : 0;
}

/* ── 动效底层回调函数 ── */
static void va_anim_set_bg_opa_cb(void *var, int32_t v) {
    lv_obj_set_style_bg_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

static void va_anim_set_y_cb(void *var, int32_t v) {
    lv_obj_set_y((lv_obj_t *)var, (lv_coord_t)v);
}

static void va_arc_rotate_cb(void *var, int32_t v) {
    if (var) {
        lv_arc_set_rotation((lv_obj_t *)var, v);
    }
}

static void ripple_anim_cb(void *var, int32_t v) {
    lv_obj_t *obj = (lv_obj_t *)var;
    if (!obj) return;
    int32_t size = 24 + (34 * v) / 100;
    lv_obj_set_size(obj, size, size);
    int32_t opa = 200 - (200 * v) / 100;
    lv_obj_set_style_opa(obj, opa, 0);
}

/* ── UTF-8 安全宽度获取 ── */
static uint32_t get_utf8_char_len(const char *s) {
    if ((s[0] & 0x80) == 0) return 1;
    if ((s[0] & 0xE0) == 0xC0) return 2;
    if ((s[0] & 0xF0) == 0xE0) return 3;
    if ((s[0] & 0xF8) == 0xF0) return 4;
    return 1;
}

/* ── 核心动效控制逻辑 ── */
static void va_start_ripple_anims(void) {
    for (int i = 0; i < 3; i++) {
        if (!ripples[i]) {
            ripples[i] = lv_obj_create(feedback_container);
            lv_obj_set_style_radius(ripples[i], LV_RADIUS_CIRCLE, 0);
            lv_obj_set_style_bg_opa(ripples[i], 0, 0);
            lv_obj_set_style_border_color(ripples[i], lv_color_hex(0x5A9EFC), 0); // 耀目霓虹蓝
            lv_obj_set_style_border_width(ripples[i], 1, 0);
            lv_obj_align(ripples[i], LV_ALIGN_CENTER, 0, 0);
        }
        lv_obj_clear_flag(ripples[i], LV_OBJ_FLAG_HIDDEN);
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, ripples[i]);
        lv_anim_set_values(&a, 0, 100);
        lv_anim_set_time(&a, 1200);
        lv_anim_set_delay(&a, i * 400); // 均匀涟漪铺开
        lv_anim_set_exec_cb(&a, ripple_anim_cb);
        lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
        lv_anim_start(&a);
    }
}

static void va_stop_ripple_anims(void) {
    for (int i = 0; i < 3; i++) {
        if (ripples[i]) {
            lv_anim_del(ripples[i], ripple_anim_cb);
            lv_obj_add_flag(ripples[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
}

/* ── 物理自释放退场回调 ── */
static void va_dismiss_anim_ready_cb(lv_anim_t *a) {
    (void)a;
    if (assistant_layer) {
        lv_obj_del(assistant_layer);
        assistant_layer = NULL;
        dialog_box = NULL;
        state_title = NULL;
        feedback_container = NULL;
        mic_icon = NULL;
        loading_arc = NULL;
        chat_container = NULL;
        user_label = NULL;
        ai_label = NULL;
        for (int i = 0; i < 3; i++) ripples[i] = NULL;
    }
}

/* 创建对话卡片 UI */
static void lv_va_create_dialog(void) {
    lv_obj_t *scr = lv_scr_act();
    assistant_layer = lv_obj_create(scr);
    /* Keep the overlay tied to the active screen size across rotation. */
    lv_obj_set_size(assistant_layer, lv_pct(100), lv_pct(100));
    lv_obj_set_style_bg_color(assistant_layer, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_grad_dir(assistant_layer, LV_GRAD_DIR_NONE, 0); // 显式关闭默认垂直渐变色以消除灰色雾霾
    lv_obj_set_style_shadow_width(assistant_layer, 0, 0); // 显式消除阴影
    lv_obj_set_style_bg_opa(assistant_layer, 0, 0); // 初始透明，由动画淡入至 180 (70% 暗化)
    lv_obj_set_style_border_width(assistant_layer, 0, 0);
    lv_obj_add_flag(assistant_layer, LV_OBJ_FLAG_HIDDEN);
    lv_obj_center(assistant_layer);

    // 对话卡片（居中、圆角、半透明深灰底）
    dialog_box = lv_obj_create(assistant_layer);
    lv_obj_update_layout(scr);
    lv_coord_t screen_w = lv_obj_get_width(scr);
    lv_coord_t screen_h = lv_obj_get_height(scr);
    lv_coord_t dialog_w = LV_MIN(screen_w - 32, 520);
    lv_coord_t dialog_h = LV_MIN(screen_h - 32, screen_h > screen_w ? 280 : 220);
    lv_obj_set_size(dialog_box, dialog_w, dialog_h);
    lv_obj_set_style_bg_color(dialog_box, lv_color_hex(0x1E1E24), 0); // 高雅深太灰色
    lv_obj_set_style_bg_opa(dialog_box, 230, 0); // ~90% 不透明度
    lv_obj_set_style_radius(dialog_box, 16, 0); // 圆角矩形
    lv_obj_set_style_border_width(dialog_box, 0, 0);
    lv_obj_set_style_pad_all(dialog_box, 8, 0); // 缩窄边距
    lv_obj_clear_flag(dialog_box, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(dialog_box, (screen_w - dialog_w) / 2, screen_h);

    // A. 顶部中文状态标签
    state_title = lv_label_create(dialog_box);
    lv_label_set_text(state_title, "语音助手");
    lv_obj_set_style_text_font(state_title, &lv_font_cjk_16, 0);
    lv_obj_set_style_text_color(state_title, lv_color_hex(0xA0A5B5), 0); 
    lv_obj_align(state_title, LV_ALIGN_TOP_MID, 0, 4); // 微降4px获得更好的视觉呼吸感

    // B. 核心反馈区容器 (60x60)
    feedback_container = lv_obj_create(dialog_box);
    lv_obj_set_size(feedback_container, 60, 60);
    lv_obj_align(feedback_container, LV_ALIGN_TOP_MID, 0, 26);
    lv_obj_set_style_bg_opa(feedback_container, 0, 0);
    lv_obj_set_style_border_width(feedback_container, 0, 0);
    lv_obj_clear_flag(feedback_container, LV_OBJ_FLAG_SCROLLABLE);

    // B1. 居中麦克风图标 label
    mic_icon = lv_label_create(feedback_container);
    lv_label_set_text(mic_icon, LV_SYMBOL_AUDIO);
    lv_obj_set_style_text_color(mic_icon, lv_color_hex(0xFFFFFF), 0);
    lv_obj_align(mic_icon, LV_ALIGN_CENTER, 0, 0);

    // B2. 科技感旋转 Loading 环
    loading_arc = lv_arc_create(feedback_container);
    lv_obj_set_size(loading_arc, 44, 44); // 缩微加载环
    lv_obj_align(loading_arc, LV_ALIGN_CENTER, 0, 0);
    lv_arc_set_angles(loading_arc, 0, 60); // 60 度单弧
    lv_arc_set_bg_angles(loading_arc, 0, 360);
    lv_obj_set_style_arc_opa(loading_arc, 30, LV_PART_MAIN);
    lv_obj_set_style_arc_color(loading_arc, lv_color_hex(0xFFFFFF), LV_PART_MAIN);
    lv_obj_set_style_arc_color(loading_arc, lv_color_hex(0x5A9EFC), LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(loading_arc, 3, LV_PART_MAIN); // 线宽也稍微减薄
    lv_obj_set_style_arc_width(loading_arc, 3, LV_PART_INDICATOR);
    lv_obj_add_flag(loading_arc, LV_OBJ_FLAG_HIDDEN);

    // C. 文本对话区容器 (Flex Column)
    chat_container = lv_obj_create(dialog_box);
    lv_obj_set_size(chat_container, dialog_w - 16, dialog_h - 104);
    lv_obj_align(chat_container, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_set_style_bg_opa(chat_container, 0, 0);
    lv_obj_set_style_border_width(chat_container, 0, 0);
    lv_obj_set_style_pad_all(chat_container, 0, 0);
    lv_obj_set_flex_flow(chat_container, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(chat_container, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);
    lv_obj_set_style_pad_row(chat_container, 2, 0); // 紧凑行距 2px
    lv_obj_clear_flag(chat_container, LV_OBJ_FLAG_SCROLLABLE);

    // C1. 用户问话 label
    user_label = lv_label_create(chat_container);
    lv_obj_set_width(user_label, lv_pct(100));
    lv_label_set_long_mode(user_label, LV_LABEL_LONG_WRAP);
    lv_label_set_text(user_label, "");
    lv_obj_set_style_text_font(user_label, &lv_font_cjk_16, 0);
    lv_obj_set_style_text_color(user_label, lv_color_hex(0xFECB2F), 0); // 暖亮橙黄色
    lv_obj_add_flag(user_label, LV_OBJ_FLAG_HIDDEN);

    // C2. AI 答复 label
    ai_label = lv_label_create(chat_container);
    lv_obj_set_width(ai_label, lv_pct(100));
    lv_label_set_long_mode(ai_label, LV_LABEL_LONG_WRAP);
    lv_label_set_text(ai_label, "");
    lv_obj_set_style_text_font(ai_label, &lv_font_cjk_16, 0);
    lv_obj_set_style_text_color(ai_label, lv_color_hex(0xFFFFFF), 0); // 洁白色
    lv_obj_add_flag(ai_label, LV_OBJ_FLAG_HIDDEN);
}

/* 打字机定时器回调 */
static void typewriter_timer_cb(lv_timer_t *t) {
    if (!t || !t->user_data) return;
    lv_obj_t *label = (lv_obj_t *)t->user_data;
    
    if (typewriter_index < strlen(typewriter_text)) {
        uint32_t len = get_utf8_char_len(&typewriter_text[typewriter_index]);
        typewriter_index += len;
        
        char *buf = malloc(typewriter_index + 1);
        if (buf) {
            memcpy(buf, typewriter_text, typewriter_index);
            buf[typewriter_index] = '\0';
            lv_label_set_text(label, buf);
            free(buf);
        }
    } else {
        lv_timer_del(t);
        typewriter_timer = NULL;
        // 打字机完成，启动无持续交互自动退场超时（4秒）
        if (exit_timer) {
            lv_timer_del(exit_timer);
        }
        exit_timer = lv_timer_create(va_exit_timer_cb, 4000, NULL);
    }
}

static void va_exit_timer_cb(lv_timer_t *t) {
    lv_timer_del(t);
    exit_timer = NULL;
    lv_va_show_state(LV_VA_STATE_EXIT);
}

/* 公共接口：初始化语音助手 UI */
void lv_va_init(void) {
    ESP_LOGI(TAG, "Initializing voice assistant UI placeholder");
    // 本地唤醒词检测初始化（占位）
    lv_va_wake_detect_init();
}

/* 显示对话框并进入指定状态 */
void lv_va_show_state(LvVaState state) {
    if (!lvgl_port_lock(500)) {
        ESP_LOGW("lv_va", "Failed to lock LVGL for show_state");
        return;
    }
    // 退出状态外，若图层缺失，立即懒加载动态创建
    if (state != LV_VA_STATE_EXIT && !assistant_layer) {
        lv_va_create_dialog();
    }
    
    /*
     * Continuous listening can begin while the previous reply is still
     * typing. Preserve that timer for intermediate states so the reply can
     * finish and schedule its normal dismissal.
     */
    if (state == LV_VA_STATE_WAKE ||
        state == LV_VA_STATE_REPLY ||
        state == LV_VA_STATE_EXIT) {
        if (typewriter_timer) {
            lv_timer_del(typewriter_timer);
            typewriter_timer = NULL;
        }
        if (exit_timer) {
            lv_timer_del(exit_timer);
            exit_timer = NULL;
        }
    }

    switch (state) {
        case LV_VA_STATE_WAKE: {
            lv_obj_clear_flag(assistant_layer, LV_OBJ_FLAG_HIDDEN);
            
            // 1. 全屏黑色遮罩淡入至 180 (70%)
            lv_anim_t a_mask;
            lv_anim_init(&a_mask);
            lv_anim_set_var(&a_mask, assistant_layer);
            lv_anim_set_values(&a_mask, 0, 180);
            lv_anim_set_time(&a_mask, 400);
            lv_anim_set_exec_cb(&a_mask, va_anim_set_bg_opa_cb);
            lv_anim_start(&a_mask);
            
            // 2. 卡片从屏幕底部进入，目标位置随横竖屏尺寸计算
            lv_anim_t a_card;
            lv_anim_init(&a_card);
            lv_anim_set_var(&a_card, dialog_box);
            lv_anim_set_values(
                &a_card, lv_obj_get_height(assistant_layer),
                va_dialog_target_y());
            lv_anim_set_time(&a_card, 550);
            lv_anim_set_path_cb(&a_card, lv_anim_path_overshoot);
            lv_anim_set_exec_cb(&a_card, va_anim_set_y_cb);
            lv_anim_start(&a_card);
            
            lv_label_set_text(state_title, "语音助手");
            lv_obj_add_flag(loading_arc, LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(mic_icon, LV_OBJ_FLAG_HIDDEN);
            break;
        }
        case LV_VA_STATE_LISTEN: {
            lv_label_set_text(state_title, "聆听中...");
            lv_obj_add_flag(loading_arc, LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(mic_icon, LV_OBJ_FLAG_HIDDEN);
            
            // 启动霓虹水波纹声波扩散
            va_start_ripple_anims();
            break;
        }
        case LV_VA_STATE_RECOGNIZE: {
            lv_label_set_text(state_title, "识别中...");
            va_stop_ripple_anims();
            
            // 由 lv_va_show_text() 设置实际识别文字
            lv_obj_clear_flag(user_label, LV_OBJ_FLAG_HIDDEN);
            break;
        }
        case LV_VA_STATE_THINK: {
            lv_label_set_text(state_title, "思考中...");
            lv_obj_add_flag(mic_icon, LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(loading_arc, LV_OBJ_FLAG_HIDDEN);
            
            // 开启加载圈高频旋转动画
            lv_anim_t a_spin;
            lv_anim_init(&a_spin);
            lv_anim_set_var(&a_spin, loading_arc);
            lv_anim_set_values(&a_spin, 0, 360);
            lv_anim_set_time(&a_spin, 1000);
            lv_anim_set_exec_cb(&a_spin, va_arc_rotate_cb);
            lv_anim_set_repeat_count(&a_spin, LV_ANIM_REPEAT_INFINITE);
            lv_anim_start(&a_spin);
            break;
        }
        case LV_VA_STATE_REPLY: {
            lv_label_set_text(state_title, "回复中...");
            lv_anim_del(loading_arc, va_arc_rotate_cb);
            lv_obj_add_flag(loading_arc, LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(mic_icon, LV_OBJ_FLAG_HIDDEN); // 复原麦克风为点缀
            
            // 由 lv_va_show_text() 设置实际 AI 回复
            lv_obj_clear_flag(ai_label, LV_OBJ_FLAG_HIDDEN);
            if (typewriter_text[0] == '\0') {
                strncpy(typewriter_text, "小智：正在生成回复...", sizeof(typewriter_text) - 1);
            }
            typewriter_index = 0;
            typewriter_timer = lv_timer_create(typewriter_timer_cb, 50, ai_label);
            break;
        }
        case LV_VA_STATE_EXIT: {
            if (assistant_layer) {
                va_stop_ripple_anims();
                lv_anim_del(loading_arc, va_arc_rotate_cb);
                
                // 1. 遮罩淡出
                lv_anim_t a_mask;
                lv_anim_init(&a_mask);
                lv_anim_set_var(&a_mask, assistant_layer);
                lv_anim_set_values(&a_mask, 180, 0);
                lv_anim_set_time(&a_mask, 400);
                lv_anim_set_exec_cb(&a_mask, va_anim_set_bg_opa_cb);
                lv_anim_start(&a_mask);
                
                // 2. 卡片滑出屏幕
                lv_anim_t a_card;
                lv_anim_init(&a_card);
                lv_anim_set_var(&a_card, dialog_box);
                lv_anim_set_values(
                    &a_card, va_dialog_target_y(),
                    lv_obj_get_height(assistant_layer));
                lv_anim_set_time(&a_card, 400);
                lv_anim_set_path_cb(&a_card, lv_anim_path_ease_in);
                lv_anim_set_exec_cb(&a_card, va_anim_set_y_cb);
                lv_anim_set_ready_cb(&a_card, va_dismiss_anim_ready_cb); // 结束后彻底删除销毁释放堆内存
                lv_anim_start(&a_card);
            }
            break;
        }
        default:
            break;
    }
    lvgl_port_unlock();
}

/* 隐藏并销毁对话框 */
void lv_va_hide_dialog(void) {
    if (!lvgl_port_lock(500)) {
        ESP_LOGW("lv_va", "Failed to lock LVGL for hide_dialog");
        return;
    }
    lv_va_show_state(LV_VA_STATE_EXIT);
    lvgl_port_unlock();
}

/* 本地唤醒词检测初始化（占位实现） */
void lv_va_wake_detect_init(void) {
    ESP_LOGI(TAG, "Wake word detection init (placeholder)");
}

/* 发送文本到云端并获取回复（占位实现） */
void lv_va_send_text_to_cloud(const char *text) {
    ESP_LOGI(TAG, "Sending text to cloud: %s", text);
    if (text && strlen(text) > 0) {
        char buf[512];
        snprintf(buf, sizeof(buf), "你：%s", text);
        lv_label_set_text(user_label, buf);
    }
    lv_va_show_state(LV_VA_STATE_REPLY);
}

/* 显示真实对话文本 */
void lv_va_show_text(const char *user_text, const char *ai_text, const char *emotion) {
    if (!lvgl_port_lock(500)) {
        ESP_LOGW("lv_va", "Failed to lock LVGL for show_text");
        return;
    }
    if (!assistant_layer) {
        lvgl_port_unlock();
        return;
    }

    if (user_text && strlen(user_text) > 0) {
        char buf[512];
        // 净化文本，替换不在字库里的字符，消灭 glyph-not-found 日志洪水
        sanitize_for_cjk_font(user_text, s_sanitized_text, sizeof(s_sanitized_text));
        snprintf(buf, sizeof(buf), "你：%.470s", s_sanitized_text);
        lv_obj_clear_flag(user_label, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(user_label, buf);
    }

    if (ai_text && strlen(ai_text) > 0) {
        // 净化文本后再送入打字机效果
        sanitize_for_cjk_font(ai_text, s_sanitized_text, sizeof(s_sanitized_text));
        snprintf(typewriter_text, sizeof(typewriter_text), "小智：%.940s", s_sanitized_text);
        typewriter_index = 0;
        lv_obj_add_flag(user_label, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(ai_label, "");
        lv_obj_clear_flag(ai_label, LV_OBJ_FLAG_HIDDEN);
        if (typewriter_timer) {
            lv_timer_del(typewriter_timer);
        }
        typewriter_timer = lv_timer_create(typewriter_timer_cb, 50, ai_label);
    }

    // 情绪驱动 UI 主题色
    lv_color_t accent;
    if (emotion) {
        if (strcmp(emotion, "empathic") == 0)      accent = lv_color_hex(0xFF8C42); // 暖橙
        else if (strcmp(emotion, "sad") == 0)      accent = lv_color_hex(0x5B9BD5); // 冷蓝
        else if (strcmp(emotion, "happy") == 0)    accent = lv_color_hex(0xFFD700); // 亮黄
        else                                       accent = lv_color_hex(0xFFFFFF); // 白
        lv_obj_set_style_border_color(dialog_box, accent, 0);
    }
    lvgl_port_unlock();
}
