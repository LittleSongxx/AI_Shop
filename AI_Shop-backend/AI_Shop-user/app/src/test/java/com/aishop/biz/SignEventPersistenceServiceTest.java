package com.aishop.biz;

import com.aishop.api.dto.SignRecordMessageDTO;
import com.aishop.entity.po.UserSignRecord;
import com.aishop.entity.po.UserSignRecordDetail;
import com.aishop.entity.query.UserSignRecordDetailQuery;
import com.aishop.entity.query.UserSignRecordQuery;
import com.aishop.mappers.UserSignRecordDetailMapper;
import com.aishop.mappers.UserSignRecordMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SignEventPersistenceServiceTest {

    @Mock
    private UserSignRecordMapper<UserSignRecord, UserSignRecordQuery> userSignRecordMapper;
    @Mock
    private UserSignRecordDetailMapper<UserSignRecordDetail, UserSignRecordDetailQuery>
            userSignRecordDetailMapper;
    @Mock
    private UserMemberProfileService userMemberProfileService;
    @InjectMocks
    private SignEventPersistenceService service;

    @Test
    void newlyInsertedSignDetailGrantsGrowthExactlyOnce() {
        when(userSignRecordDetailMapper.insertIgnore(any())).thenReturn(1);

        assertTrue(service.persist(message(), 5));

        verify(userSignRecordMapper).insertOrUpdate(any(UserSignRecord.class));
        verify(userMemberProfileService).addGrowth("u1", 5);
    }

    @Test
    void duplicateSignDetailDoesNotGrantGrowthAgain() {
        when(userSignRecordDetailMapper.insertIgnore(any())).thenReturn(0);

        assertFalse(service.persist(message(), 5));

        verify(userSignRecordMapper, never()).insertOrUpdate(any(UserSignRecord.class));
        verify(userMemberProfileService, never()).addGrowth(any(), any(Integer.class));
    }

    private static SignRecordMessageDTO message() {
        return new SignRecordMessageDTO("u1", 3, 10, 0, "20260815", 0);
    }
}
