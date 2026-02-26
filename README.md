# roscamp-repo-2
ROS2와 AI를 활용한 자율주행 로봇개발자 부트캠프 2팀 저장소. ROS2 기반 자율 시스템 무인 택시 충전·운영 통합 플랫폼 (AutoServe)
<img width="1080" height="1080" alt="로고" src="https://github.com/user-attachments/assets/92fddc97-94ce-4efc-8d83-4f9b04b3f1e3" />
<h1 align="center">🚖 TASHO</h1>

<p align="center">2-PRO 팀이 개발한, AI 기반 자율 시스템 무인 택시 충전·운영 통합 시스템입니다.</p>


# 📌 프로젝트 개요 (Project Scenario)
본 프로젝트는 ROS 2를 기반으로 자율주행 무인 택시 시스템을 설계하고 구현하는 것을 목표로 한다. 본 시스템은 자율주행 이동 기능을 중심으로, 배터리 상태 인식과 자동 충전 기능을 통합하여 사람의 개입 없이 지속적으로 운용 가능한 무인 이동 플랫폼을 구현한다.
본 프로젝트에서는 자율주행 로봇, 로봇팔을 활용한 충전 시스템, 사용자 목적지 선택 인터페이스를 하나의 통합 구조로 구성하며, 이를 통해 실제 무인 택시 운영 환경을 가정한 시스템 구현 가능성을 검증하고자 한다.

- **프로젝트명**: TASHO
- **팀명**: 2-PRO
- **주제**: AI 기반 자율 시스템 무인 택시 충전·운영 통합 시스템
- **핵심 기술**: ROS2, YOLO, OpenCV, UDP, TCP, HTTP

# 👥 팀 구성 및 역할 (Team Roles)
|        | NAME | JOB |
|:------:|:----:|:----:|
| Leader  | 조건희 | Project manager, Pinky localization |
| Worker   | 김다준 | Admin, User GUI |
| Worker   | 박건우 | Pinky Yolo,GUI monitoring |
| Worker   | 정현준 | Jetcobot |
| Worker   | 최원준 | DB,Main Server |
| Worker   | 함주현 | HSV Detection, 문서 작성, PPT 제작, |
# ⚙️ 기술 스택 (Tech Stack)
- **Hardware Platform**:Raspberry Pi(PinkyBot, Jetcobot)
- **Development Environment**: Ubuntu(24.04) ROS2(Jazzy), OpenCV, YOLOv8, PyQt
- **Programming Languages**:Python, MySQL
- **Sensors & Perception Devices**LiDAR, Camera, IR Sensor
- **Communication Protocols**: TCP, UDP, HTTP
- **TOS2 Middleware & Messaging**: ROS2 Domain Bridge
- **Configuration Management**: Github, Jira, Confluence, Slack

# 📁 시스템 구성 (System Architecture)

## Structural Diagram
<img width="804" height="352" alt="구조도 drawio" src="https://github.com/user-attachments/assets/21cdb1ea-ed1f-41e9-b1f9-717c0325d4e1" />

## Hardware Architecture
<img width="1446" height="1141" alt="HW Architecture1111111111111 drawio" src="https://github.com/user-attachments/assets/e555bb18-0cfb-4408-b84c-daee5557fade" />

## Software Architecture
<img width="1570" height="1048" alt="Software Architecture drawio (1)" src="https://github.com/user-attachments/assets/d5b222e0-1da4-4a4f-8ecd-8e2f916701b9" />

## System Architecture
<img width="1510" height="974" alt="시스아키 drawio" src="https://github.com/user-attachments/assets/0f6016dc-48cb-4e28-bbde-db6ddd6ac5fe" />

## Sequence Diagram
<img width="735" height="960" alt="무인택시운행 drawio" src="https://github.com/user-attachments/assets/579fe168-b0d9-44d6-97bb-ceb087fd59a7" />
<img width="780" height="940" alt="무인택시충전 drawio" src="https://github.com/user-attachments/assets/a3f49a4e-552f-4bf4-914e-8d1efa5263f5" />



## Map
<img width="998" height="498" alt="캡" src="https://github.com/user-attachments/assets/aa64f28b-5bc2-4090-a59e-82adfe0bde2f" />



# 🛠 Implementation
## Scenario
### 시나리오 1 - 무인 택시 호출
![시나리오 1 호출](https://github.com/user-attachments/assets/3883a51b-1379-436a-a064-79145245c792)
<img src="images/gui.png" alt="GUI" width="800" />
<img src="images/wngod.png" alt="GUI" width="800" />

### 시나리오 2 - 무인 택시 주행
![시나리오 2 주행](https://github.com/user-attachments/assets/e0103423-531c-4a1d-9bd8-88ff34d97043)


### 시나리오 3 - 로봇팔 충전 
**조건:** 배터리 ≤ 30%
![시나리오 3 로봇팔 충전](https://github.com/user-attachments/assets/0390a252-9746-4ba2-ac99-fc3be3f6aa96)


# 🎥 Demonstration Video
## 영상 제목
(영상)


