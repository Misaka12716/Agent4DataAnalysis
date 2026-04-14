@bp_auth.route("/login_with_sms", methods=["POST"])
def login_with_sms():
    """
    Login with SMS verification code.

    Request Args:
        phone (str): The phone number.
        code (str): The verification code.

    Return Code:
        0: Success.
        1: Invalid request.
        2: Invalid parameter data.
        3: Permission denied.
        4: User is blocked.
        5: Verification code is incorrect or expired.
    """
    phone = req_get("phone")
    # 检查必填字段
    if not phone:
        elk_logger.info(
            "User login with SMS but phone is empty",
            extra={"status": "FAILED", "code": 1, "phone": phone},
        )
        return formatted_response(code=1, msg="missing parameters: phone is empty")

    # 检查手机号是否已注册
    user = User.query.filter_by(phone=phone).first()
    if not user:
        elk_logger.info(
            "User login with SMS but user not found",
            extra={"status": "FAILED", "code": 2, "phone": phone},
        )
        return formatted_response(code=2, msg="user not found")

    # 检查用户是否被禁用
    if user.is_blocked == True:
        elk_logger.info(
            "User login with SMS but user is blocked",
            extra={"status": "FAILED", "code": 3, "phone": phone},
        )
        return formatted_response(code=3, msg="user is blocked")

    # 登录用户
    login_user(user)
    elk_logger.info(
        "User login with SMS",
        extra={"status": "SUCCESS", "code": 0, "phone": phone},
    )
    return formatted_response(code=0, msg="login success")
@bp_auth.route("/send_sms_code", methods=["POST"])
def send_sms_code():
    """
    Send SMS verification code.

    Request Args:
        phone (str): The phone number.

    Return Code:
        0: Success.
        1: Invalid request.
        2: Error sending SMS.
    """
    import random
    import hashlib
    import requests
    import time
    from datetime import datetime

    app_id = "EUCP-EMY-SMS1-05RA6"
    secret_key = "F1D5A562AED35AE3"
    sign_name = "【六元空间】"  # 替换为实际的短信签名
    sms_url = "http://bjksmtn.b2m.cn:80/simpleinter/sendSMS"
    phone = req_get("phone")
    if not phone:
        elk_logger.info(
            "Send SMS code but missing parameters",
            extra={"status": "FAILED", "code": 1, "phone": phone},
        )
        return formatted_response(code=1, msg="missing parameter: phone is empty")

    # 生成6位随机验证码
    verification_code = random.randint(100000, 999999)

    # 构造短信内容
    content = f"您的验证码是{verification_code}，有效期为2分钟。"

    # 构造请求参数
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw_string = f"{app_id}{secret_key}{timestamp}"
    sign = hashlib.md5(raw_string.encode()).hexdigest()
    params = {
        "appId": app_id,
        "timestamp": timestamp,
        "sign": sign,
        "mobiles": phone,
        "content": f"{sign_name}{content}",
    }

    # 发送短信
    try:
        response = requests.post(sms_url, data=params)
        response.raise_for_status()  # 检查HTTP请求是否成功
        response_json = response.json()
        if response_json.get("code") == "SUCCESS":
            new_code = VerificationCode(phone=phone, code=verification_code)
            db.session.add(new_code)
            db.session.commit()
            # 返回验证码给前端
            elk_logger.info(
                "Send SMS code",
                extra={"status": "SUCCESS", "code": 0, "phone": phone},
            )
            return formatted_response(code=0, msg="SMS code sent successfully")
        else:
            msg = response_json.get("msg")
            elk_logger.info(
                "Send SMS code but failed",
                extra={
                    "status": "FAILED",
                    "code": 4,
                    "phone": phone,
                    "unexpected_err": msg,
                },
            )
            return formatted_response(code=4, msg=f"Failed to send SMS: {msg}")
    except requests.RequestException as e:
        elk_logger.info(
            "Send SMS code but unexpected error",
            extra={
                "status": "FAILED",
                "code": 4,
                "phone": phone,
                "unexpected_err": str(e),
            },
        )
        return formatted_response(code=4, msg=f"Error sending SMS: {str(e)}")