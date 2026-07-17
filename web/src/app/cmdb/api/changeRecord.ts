import useApiClient from '@/utils/request';

export const useChangeRecordApi = () => {
  const { get } = useApiClient();

  // 获取变更记录列表
  const getChangeRecords = (params?: any) =>
    get('/cmdb/api/change_record', { params });

  // 获取 CMDB 首页最近资产变更摘要
  const getHomeRecentChanges = (params?: any) =>
    get('/cmdb/api/change_record/home_recent/', { params });

  // 获取指定实例的变更记录
  const getInstanceChangeRecords = (params: any) =>
    get('/cmdb/api/change_record/', { params });

  // 获取变更记录详情
  const getChangeRecordDetail = (recordId: string | number) =>
    get(`/cmdb/api/change_record/${recordId}/`);

  // 获取变更记录枚举数据
  const getChangeRecordEnumData = () =>
    get('/cmdb/api/change_record/enum_data/');

  // 获取变更场景枚举
  const getChangeRecordScenarioEnum = () =>
    get('/cmdb/api/change_record/enum_scenarios/');

  // 导出变更记录（按当前过滤条件）
  const exportChangeRecords = (params?: any) =>
    get('/cmdb/api/change_record/export/', { params, responseType: 'blob' });

  return {
    getChangeRecords,
    getHomeRecentChanges,
    getInstanceChangeRecords,
    getChangeRecordDetail,
    getChangeRecordEnumData,
    getChangeRecordScenarioEnum,
    exportChangeRecords,
  };
};
